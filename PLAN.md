# SwipeBuddy – projektplan

Status: **godkänd och implementerad** (B1-a YOLOX-Nano/MIT, B3 Edge, B4 qwen2.5vl:7b). Manuell E2E-checklista: se TESTING.md.

## 1. Mål och avgränsning

SwipeBuddy är ett lokalt Windows-program som läser profilbilder på tinder.com i en Playwright-styrd webbläsare, låter en lokal VLM bedöma dem mot användarens egna preferenser och föreslår (eller, i Auto-läge, utför) en swipe med piltangenterna.

**Ingår inte (medvetet):**
- Ingen hantering av lösenord, inloggning eller kontouppgifter. Användaren loggar in själv i webbläsarfönstret.
- Inga åtgärder mot botdetektering utöver det som efterfrågats (dagstak, slumpade fördröjningar, pauser). Ingen fingerprint-förfalskning och ingen CAPTCHA-hantering. Om Tinder visar CAPTCHA, verifiering eller ett okänt läge stoppar motorn och ber användaren ta över.
- Inga meddelanden, ingen chatt, inga Super Likes och inga köp.
- Ingen molntjänst, ingen telemetri.

## 2. Mål- och utvecklingsmiljö

| | Målmaskin | Utvecklingsmaskin (här) |
|---|---|---|
| OS | Windows 11 Home | Windows 11 |
| GPU | RTX 3060 12 GB | ingen NVIDIA-drivrutin hittad |
| Ollama | installeras vid första start | ej installerat |
| Python | – (exe) | 3.12 (venv), 3.14 finns men används inte |

Följden blir att alla enhetstester körs utan GPU, utan Ollama och utan nätverk (med mockar och fakes). GPU, Ollama och Tinder testas manuellt på målmaskinen via checklistan i avsnitt 10.

**Python 3.12** väljs för att PyInstaller, PySide6, onnxruntime och Playwright har stabila hjul för den versionen. CI använder samma version.

## 3. Diskbudget (mål ≤ ~8 GB modeller)

| Komponent | Storlek | Kommentar |
|---|---|---|
| VLM `qwen2.5vl:7b` (Q4_K_M) | ~6,0 GB | standard, kan bytas i inställningarna |
| Alternativ: `qwen2.5vl:3b` | ~3,2 GB | snabbare, sämre bedömning |
| Personsdetektor (ONNX) | 4–13 MB | se beslut B1 |
| **Summa modeller** | **~6,0 GB** | inom budget |
| Ollama-programmet | ~1,5–2 GB | CUDA-bibliotek ingår |
| SwipeBuddy (onedir) | ~250–400 MB | **ingen torch**, se B1 |
| Webbläsare | 0 MB | Edge som redan finns i Windows 11, se B3 |

Totalt cirka 8–8,5 GB, alltså väl inom de 59 GB som finns lediga.

VRAM: qwen2.5vl:7b med 2–3 bilder på max 768 px tar ungefär 8–9 GB, vilket ryms på 12 GB. Detektorn körs på CPU så att GPU:n bara används av VLM:en.

## 4. Beslut som behöver ditt godkännande

**B1. Licenskonflikt: YOLOv8n (AGPL-3.0) och MIT.**
`ultralytics` och YOLOv8-vikterna är AGPL-3.0. En exe som paketerar dem blir AGPL-täckt, vilket krockar med kravet på MIT. Dessutom drar `ultralytics` in torch, så exe:n växer med 0,7–2,5 GB.

| Alternativ | Licens | Exe-storlek | Avvikelse från spec |
|---|---|---|---|
| **B1-a (rekommenderas):** YOLOX-Nano (Apache-2.0) som ONNX via `onnxruntime` på CPU | MIT behålls | liten | byter YOLOv8n mot en likvärdig liten detektor |
| B1-b: behåll YOLOv8n som ONNX via `onnxruntime` och byt projektlicens till AGPL-3.0 | AGPL-3.0 | liten | byter licens |
| B1-c: behåll `ultralytics` och MIT | juridiskt tveksamt | stor | – |

Detektorn ligger bakom ett gränssnitt (`PersonDetector`), så backend kan bytas senare utan att resten av koden påverkas.

**B2. Git-identitet.** Repot är publikt och commit-metadata publiceras. Jag föreslår att repot får en lokal `user.email` med din GitHub-*noreply*-adress (`<id>+<användarnamn>@users.noreply.github.com`). Jag behöver ditt GitHub-användarnamn innan första commit.

**B3. Webbläsare.** Standard är Playwright med `channel="msedge"`. Edge är Chromium-baserat och redan installerat, så inget behöver laddas ner. Om Edge saknas används Playwrights egen Chromium, som laddas ner vid första start (~150 MB, med progressbar).

**B4. VLM-modell.** Standard är `qwen2.5vl:7b`, och modellen kan bytas i inställningarna (`qwen2.5vl:3b`, `qwen3-vl:8b` om den finns i Ollama). Ollamas `format` med JSON-schema används, vilket tvingar fram giltig JSON redan vid generering. Parsningen validerar ändå allt, och ogiltigt svar ger pass.

## 5. Arkitektur

```
┌──────────────── GUI-tråd (PySide6) ────────────────┐
│ MainWindow: Live-flik │ Inställningar │ Statistik   │
│ SetupDialog (första start)                          │
└───────▲───────────────────────────────┬────────────┘
        │ Qt-signaler (EngineEvent)     │ kommandon: start/stop/confirm
┌───────┴───────────────────────────────▼────────────┐
│ EngineWorker (QThread) – äger ALLT Playwright-arbete│
│  SwipeEngine (ren logik, testbar utan Qt)           │
│   ├─ TinderBrowser   (Playwright, sync API)         │
│   ├─ PersonDetector  (onnxruntime, CPU)             │
│   ├─ VlmJudge        (HTTP → localhost:11434)       │
│   ├─ DecisionPolicy  (tröskel + regler)             │
│   ├─ Pacer           (fördröjning, pauser, dagstak) │
│   └─ StatsStore      (endast antal like/pass/dag)   │
└────────────────────────────────────────────────────┘
```

Playwrights sync-API är trådbundet, så webbläsaren skapas, används och stängs i samma arbetstråd. GUI:t pratar bara med motorn via signaler och en kommandokö.

### Dataflöde per profil
1. `browser.capture_profile_images()` tar skärmdumpar av kortet, bläddrar med mellanslag och stoppar när bilden inte ändras eller vid max 6 bilder. Resultatet är `list[PIL.Image]` som bara finns i RAM.
2. `detector.crop_person(img)` returnerar den största personen med 10 % marginal, eller `None`. Bilder utan person hoppas över.
3. `judge.judge(crops[:3], prefs)` returnerar `Verdict`. Bilderna nedskalas till max 768 px och skickas som base64 över localhost.
4. `policy.decide(verdict, threshold)` returnerar `like` eller `pass`. Om inga personer hittades blir det `pass` med orsaken "ingen person hittad".
5. I läget **Förslag** visas beslutet och motorn väntar på användarens knapp (Like / Pass / Hoppa över). I läget **Auto** används beslutet direkt.
6. `pacer.wait_before_swipe()` väntar, sedan körs `browser.swipe(decision)` och `stats.record(decision)`.
7. Alla bildreferenser släpps (`del`) innan nästa profil.

## 6. Moduler och gränssnitt

Paketet heter `src/swipebuddy/`. Varje modul får ett eget testfil under `tests/`.

### M1. config (+ paths, stats, logging)
```python
# paths.py
def app_dir() -> Path            # %LOCALAPPDATA%\SwipeBuddy (skapas vid behov)
def config_path() -> Path        # app_dir()/config.json
def profile_dir() -> Path        # app_dir()/browser-profile
def models_dir() -> Path         # app_dir()/models
def logs_dir() -> Path           # app_dir()/logs

# config.py
@dataclass
class Preferences:
    free_text: str = ""
    attributes: dict[str, bool]   # t.ex. {"glasögon_ok": True, "tatueringar_ok": True, ...}
    age_hint_min: int | None; age_hint_max: int | None   # bara som text-hint till VLM
@dataclass
class AppConfig:
    version: int = 1
    preferences: Preferences
    threshold: int = 70            # 0–100
    mode: Literal["suggest", "auto"] = "suggest"
    daily_cap: int = 100
    delay_min_s: float = 3.0; delay_max_s: float = 9.0
    pause_every_min: int = 15; pause_every_max: int = 30
    pause_min_s: float = 60; pause_max_s: float = 240
    vlm_model: str = "qwen2.5vl:7b"
    ollama_url: str = "http://127.0.0.1:11434"   # bara localhost tillåts
    browser_channel: Literal["msedge", "chromium"] = "msedge"
    max_images_per_profile: int = 6
def load_config(path: Path | None = None) -> AppConfig    # saknas/trasig → standardvärden
def save_config(cfg: AppConfig, path: Path | None = None) # atomisk skrivning (tmp + replace)
def validate(cfg: AppConfig) -> AppConfig                 # klampar intervall, min ≤ max

# stats.py
class StatsStore:                # app_dir()/stats.json: {"YYYY-MM-DD": {"like": n, "pass": n}}
    def record(self, decision: Literal["like","pass"]) -> None
    def today(self) -> dict[str, int]
    def today_total(self) -> int

# logging_setup.py
def setup_logging() -> None      # roterande fil i logs_dir(); PrivacyFilter som blockerar
                                 # allt utom vitlistade händelsenamn och siffror
```
`ollama_url` valideras så att bara `localhost`/`127.0.0.1` accepteras, vilket ger ingen extern trafik av misstag.

### M2. browser
```python
class TinderBrowser:
    def __init__(self, profile_dir: Path, channel: str, page_factory=None)  # page_factory för tester
    def start(self) -> None                  # launch_persistent_context, headful,
                                             # args=["--disk-cache-size=1"] → bilder cachas inte på disk
    def state(self) -> BrowserState          # LOGGED_OUT | READY | NO_PROFILES | MODAL | BLOCKED | UNKNOWN
    def dismiss_modal(self) -> bool          # Escape för "It's a match", uppsäljning m.m.
    def capture_profile_images(self, max_images: int) -> list[Image.Image]
    def swipe(self, decision: Literal["like","pass"]) -> None   # ArrowRight / ArrowLeft
    def close(self) -> None
```
- Alla CSS-selektorer och URL:er ligger i `tinder_selectors.py`, så att det bara är en fil att uppdatera när Tinder ändrar sin DOM.
- Skärmdumpen tas på kortelementet. Om elementet inte hittas används ett utsnitt av viewportens centrum.
- `BLOCKED` betyder CAPTCHA eller verifiering och gör att motorn stoppar.
- Tester körs mot en `FakePage` (ingen riktig webbläsare): tangenttryck, stopp när bilden inte ändras, maxantal, tillståndsigenkänning från HTML-fixtures.

### M3. detection
```python
class PersonDetector(Protocol):
    def detect(self, img: Image.Image) -> list[Box]          # Box(x1,y1,x2,y2,score)
def crop_person(img, boxes, margin=0.10, min_area_frac=0.05) -> Image.Image | None
class OnnxPersonDetector:                                    # YOLOX-Nano eller YOLOv8n beroende på B1
    def __init__(self, model_path: Path, conf=0.4, iou=0.5)
```
- För- och efterbehandling (letterbox, decode, NMS) skrivs i numpy och testas med syntetiska tensorer.
- Tester täcker NMS, val av största box, marginal och klampning mot bildkanten, och "ingen person" ger `None`. En onnx-modell behövs inte i enhetstesterna.

### M4. vlm
```python
@dataclass(frozen=True)
class Verdict:
    decision: Literal["like","pass"]; score: int; reason: str; valid: bool
def build_prompt(prefs: Preferences) -> str
def parse_verdict(raw: str) -> Verdict      # strikt: exakt 3 nycklar, rätt typer, 0–100,
                                            # reason ≤ 200 tecken; allt annat → pass, valid=False
class OllamaClient:
    def __init__(self, base_url: str, timeout_s: float = 120)
    def is_running(self) -> bool
    def version(self) -> str | None
    def has_model(self, name: str) -> bool
    def pull(self, name: str, on_progress: Callable[[int,int,str],None]) -> None  # streamad /api/pull
    def chat(self, model, prompt, images_b64, schema) -> str
class VlmJudge:
    def __init__(self, client: OllamaClient, model: str)
    def judge(self, crops: list[Image.Image], prefs: Preferences) -> Verdict      # aldrig undantag → pass
```
- `temperature=0` och `keep_alive="10m"`. Prompten instruerar modellen att bara bedöma synligt utseende mot preferenserna, och att svara pass vid osäkerhet.
- Tester använder `httpx.MockTransport` för hälsa, pull-progress, chat-svar, timeout och HTTP-fel, plus ett stort antal tabelldrivna fall för `parse_verdict` (markdown-staket, extra nycklar, score som sträng, NaN, tomt svar).

### M5. engine (beslutsmotor)
```python
def decide(v: Verdict, threshold: int) -> Literal["like","pass"]   # like endast om valid ∧ like ∧ score ≥ threshold
class Pacer:
    def __init__(self, cfg: AppConfig, rng: random.Random, sleep: Callable[[float], None])
    def wait_before_swipe(self, stop: threading.Event) -> None   # avbrytbar
    def maybe_pause(self, stop: threading.Event) -> float        # sekunder pausat
class SwipeEngine:
    def __init__(self, cfg, browser, detector, judge, stats, events: Callable[[EngineEvent],None],
                 commands: queue.Queue, rng, sleep)
    def run(self, stop: threading.Event) -> None
EngineEvent = Status | Evaluating | VerdictReady | AwaitingConfirm | Swiped | Paused
            | CapReached | NeedsUser(reason) | Error(msg_utan_profildata)
```
- Dagstaken kontrolleras före varje profil och räknas via `StatsStore`, så den överlever omstart.
- I Förslag-läget väntar motorn på `Confirm(decision)` eller `Skip` från kommandokön. Stopp avbryter väntan direkt.
- Tester använder fakes för alla beroenden plus deterministisk `rng`/`sleep`. De täcker tröskelgränser, att dagstaken stoppar, att pauser kommer inom intervallet, Förslag-flödet, Auto-flödet, att `BLOCKED` stoppar, och att ett undantag i VLM ger pass i stället för krasch.

### M6. GUI (PySide6) + första start
```
gui/main_window.py   – flikar: Live | Preferenser | Inställningar
gui/live_tab.py      – status, beslut, score (stapel), motivering, Start/Stopp,
                       Förslag-knappar (Like / Pass / Hoppa över), dagens like/pass/tak
gui/prefs_tab.py     – fritext + kryssrutor/reglage → config.json (Spara)
gui/settings_tab.py  – tröskel, läge (Auto kräver bekräftelsedialog), dagstak, fördröjningar,
                       pauser, VLM-modell, webbläsarkanal
gui/setup_dialog.py  – första start: 1) Ollama igång? annars instruktion + länk till ollama.com
                       2) GPU (nvidia-smi) + VRAM-varning < 8 GB  3) modellnedladdning med
                       progressbar (VLM via Ollama, detektor via HTTPS, ev. Chromium)
gui/worker.py        – QThread som kör SwipeEngine och översätter EngineEvent → Qt-signaler
setup_check.py       – ren logik för kontrollerna ovan (testbar utan Qt)
app.py / __main__.py – startpunkt
```
- Motiveringen från VLM:en visas bara i GUI:t och loggas aldrig.
- Tester: `setup_check` testas med mockad subprocess och HTTP. GUI-röktester körs med `pytest-qt` i `QT_QPA_PLATFORM=offscreen` (fönstret skapas, inställningar sparas och laddas, signaler uppdaterar etiketter).

### M7. paketering
- `build.spec`: PyInstaller **onedir**, `console=False`, ikon från `assets/`. Inga modeller och ingen Chromium bakas in. Playwrights driver (node) följer med via PyInstaller-hook.
- `.github/workflows/release.yml`: körs vid tagg `v*` på `windows-latest` med Python 3.12. Stegen är `pip install` → `pytest` → `pyinstaller build.spec`. Resultatet zippas till `SwipeBuddy-<tag>-win64.zip` och laddas upp till Releases.
- `.github/workflows/ci.yml`: pytest vid push och PR.
- `tools/scan_personal_data.py` söker efter e-postadresser, `C:\Users\<namn>`, `/Users/<namn>`, användarnamn från miljön och en lista med förbjudna ord. Skriptet körs före varje commit (och i CI), och en träff stoppar committen.

## 7. Repo-struktur

```
src/swipebuddy/ {__init__, __main__, app, paths, config, stats, logging_setup,
                 browser, tinder_selectors, detection, vlm, engine, setup_check, gui/}
tests/          {test_config, test_stats, test_logging_privacy, test_browser, test_detection,
                 test_vlm, test_engine, test_setup_check, test_gui_smoke, fixtures/}
assets/         {icon.ico, icon.png}
tools/          {scan_personal_data.py}
.github/workflows/{ci.yml, release.yml}
README.md  LICENSE  requirements.txt  requirements-dev.txt  build.spec  pyproject.toml  .gitignore
```
`.gitignore` ska täcka `config.json`, `stats.json`, `browser-profile/`, `models/`, `*.onnx`, `*.pt`, `*.gguf`, `logs/`, `*.log`, `.env`, `dist/`, `build/`, `.venv/` och `__pycache__/`. Det gäller även om allt användardata normalt hamnar i `%LOCALAPPDATA%`.

**Beroenden (runtime):** `PySide6`, `playwright`, `onnxruntime`, `numpy`, `Pillow`, `httpx`.
**Dev:** `pytest`, `pytest-qt`, `pyinstaller`.

## 8. Integritet – hur kraven uppfylls

| Krav | Åtgärd | Verifieras av |
|---|---|---|
| Bilder aldrig till disk | `screenshot()` utan `path`, bara bytes → PIL i RAM; Chromium startas med `--disk-cache-size=1` | test som kontrollerar att `path` aldrig skickas; manuell kontroll av profilmappens storlek |
| Inga namn, texter eller motiveringar i loggar | `PrivacyFilter` vitlistar händelsenamn och heltal; ingen `print` av VLM-svar | `test_logging_privacy` |
| Ingen telemetri eller externa API:er | Bara `tinder.com` (webbläsaren), `127.0.0.1:11434` (Ollama), modell-URL:er vid nedladdning; `ollama_url` låst till localhost | test av URL-validering; grep i CI efter okända domäner |
| Inga lösenord | Programmet läser och skriver aldrig formulärfält; inloggning sker manuellt | kodgranskning |
| Ollama-loggar | Ollama loggar inte bilder som standard. README avråder från `OLLAMA_DEBUG=1` | README |

## 9. Risker

| Risk | Sannolikhet | Hantering |
|---|---|---|
| Kontot stängs (brott mot Tinders villkor) | hög vid Auto | Förslag-läge som standard, dagstak, pauser, tydlig disclaimer i README och i första-start-dialogen |
| Tinder ändrar DOM eller tangentbordsgenvägar | hög över tid | selektorer i en fil, reservväg via viewport-utsnitt, motorn stoppar vid `UNKNOWN` |
| CAPTCHA eller verifiering | medel | motorn stoppar, användaren tar över (ingen kringgång) |
| VLM-latens 3–10 s per profil | medel | max 3 utsnitt à 768 px, `keep_alive`; latensen räknas in i fördröjningen |
| VLM bedömer inkonsekvent eller partiskt | medel | `temperature=0`, ogiltigt eller osäkert svar ger pass, användaren ser motiveringen i Förslag-läget |
| Tredje parts integritet (personer som inte samtyckt bedöms av AI) | – | allt lokalt, ingenting sparas, ingen delning; nämns i README |
| PyInstaller och Playwright-driver | medel | onedir, explicit hook, röktest i CI att exe:n startar (`--selftest`-flagga) |
| Licens (B1) | säker om inget görs | beslut B1 |
| Ingen GPU på utvecklingsmaskinen | säker | allt mockas i enhetstester; GPU och Tinder i manuell checklista |

## 10. Arbetsordning och granskning

1. **(nu)** PLAN.md → ditt godkännande och besked om B1–B4.
2. `git init`, skelett (pyproject, .gitignore, LICENSE, venv 3.12).
3. Modul för modul, varje modul av en egen **Sonnet-subagent** med avgränsat uppdrag och gränssnitten ovan:
   **M1 config → M2 browser → M3 detektion → M4 VLM → M5 beslutsmotor → M6 GUI → M7 paketering**.
4. Efter varje modul granskar jag (Opus) koden mot gränssnittet och integritetskraven, kör `pytest` och personuppgiftsskanningen och committar. Först därefter påbörjas nästa modul.
5. README (disclaimer, installation, integritet) och en end-to-end-checklista för manuell test på målmaskinen.

## 11. End-to-end-checklista (utkast, finslipas i sista steget)

- [ ] Packa upp zip och starta `SwipeBuddy.exe` utan Python installerat
- [ ] Första start: Ollama saknas ger tydlig instruktion; efter installation går kontrollen igenom
- [ ] GPU visas som RTX 3060 12 GB
- [ ] Modellnedladdning visar progress och kan avbrytas och återupptas
- [ ] Edge öppnas; logga in manuellt; starta om programmet och kontrollera att inloggningen finns kvar
- [ ] Förslag-läge: beslut, score och motivering visas; inget swipas förrän knappen trycks
- [ ] Bilder bläddras (mellanslag) och tidsåtgången per profil är rimlig
- [ ] Ändra tröskeln och se att beslut ändras därefter
- [ ] Auto-läge kräver bekräftelse; fördröjningar på 3–9 s och pauser märks
- [ ] Dagstak: sätt till 3 och kontrollera att programmet stoppar efter 3 och fortfarande stoppar efter omstart
- [ ] "It's a match" och andra popup-fönster stängs eller pausar motorn
- [ ] Stopp avbryter direkt, även mitt i en fördröjning
- [ ] `%LOCALAPPDATA%\SwipeBuddy\logs` innehåller bara antal och händelsenamn
- [ ] Inga bildfiler har skapats (sök efter *.png/*.jpg/*.webp som ändrats under sessionen)
- [ ] Nätverk (t.ex. Resursövervakaren): bara tinder.com/CDN, localhost och modellnedladdning
