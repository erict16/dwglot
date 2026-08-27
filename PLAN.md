# 图译 / Dwglot plan

**Fork Honsen, not greenfield:** [etianwang/CAD_translator](https://github.com/etianwang/CAD_translator) (MIT, v1.8.8). pywebview + FastAPI + ezdxf + user-installed ODA File Converter.

That repo is already a Win/Mac desktop CAD text translator with batch, glossary, and DWG via ODA. Dwglot is a rebranded fork: v0.1 is TEXT/MTEXT/attribs, multilingual engines, Mac 轻语-genre UI, and auto-update from GitHub Releases. Dims/tables and signed installers are v0.2.

轻语 CAD Translator is the public-site product bar only ([qingyucad.com](https://www.qingyucad.com/)). Do not download, unpack, or decompile it.

Checked 2026-08-27 against Honsen GitHub and 轻语’s public pages.

---

## Product

- Chinese name: **图译**
- English / repo: **Dwglot**
- Open-source desktop app in the 轻语 genre: translate text inside DWG/DXF, no AutoCAD.
- Windows x64 + macOS Intel + Apple Silicon.
- MIT (keep Honsen’s MIT notice; add Dwglot copyright).

v0.1 is a usable fork of Honsen (default zh→en, more langs available), not a rewrite. UI follows the Mac sketches in `sketches/01.html` (常规处理).

---

## Why this fork

Honsen already has the pipeline we would otherwise write:

`DWG → ODA → DXF (ACAD2010) → ezdxf mutate → DXF → ODA → DWG` (DXF skips ODA).

It already walks model space, paperspace, INSERT attribs, title-block text inside block refs, optional full block scan, and `*T` / `*D` anonymous blocks. Batch queue with pause/resume/retry lives in `backend/queue.py`. DeepL + Azure live in `backend/translator.py` and `backend/providers/azure.py`. Glossary + TM in SQLite + YAML + project JSON.

What Honsen is not, and what the fork changes:

- Branded Honsen / 中法英, default `zh_to_fr`.
- Optional licence overlay (`backend/licensing.py`, `license_public_key.txt`, `tools/` keygen). `LICENSE_ENFORCEMENT_ENABLED = False` today; still strip payment QR, trial registry, and keygen from the Dwglot tree.
- README/installer tell packagers to copy ODA into `dist/ODAFileConverter/` or embed `ODAFileConverter.dmg`. **Do not ship ODA.** Detect user install only.
- SHX handling is incomplete (ODA `\M+xxxx` GBK decode + wrap MTEXT in `{\fSimSun…}`). v0.1 must rewrite STYLE to a Unicode TTF.
- MTEXT write currently rebuilds the string with a font group and drops inline codes. Fork should keep `\P` `\C` `\H` etc. when translating.
- Dims and tables are already in `CADChineseTranslator` (`DIMENSION` override, `ACAD_TABLE` group 302). v0.1 does not ship them as supported. Gate them off for v0.1 QA; turn them on in v0.2.
- Auto-update in v0.1: GitHub Releases (`erict16/dwglot`) + Sparkle (Mac) / WinSparkle (Win), with a Python fallback that opens the new dmg/zip/exe until Sparkle is signed. Unsigned first; certs later quiet Gatekeeper/SmartScreen. No Authenticode/notarize in v0.1.

No other GitHub app is a better fork. ezdxf is a library. bimwright/dwg-mcp needs AutoCAD. CAD Studio TRANS is closed and AutoCAD-bound.

---

## Stack (keep Honsen)

Do not switch to Tauri, Electron, or .NET for v0.1.

| Layer | Honsen file / tool | Dwglot |
|---|---|---|
| Entry | `run.py` | Keep. Rename window title to 图译 / Dwglot. |
| Shell | `desktop/launcher.py` pywebview (Win WebView2, macOS native webview) + uvicorn on `127.0.0.1` | Keep. Rebrand mutex / title. |
| Native | `desktop/native_bridge.py` | Keep (file dialogs, reveal in Finder/Explorer). |
| API | `backend/api.py` FastAPI | Keep. Drop licence/activate routes from the UI. |
| CAD I/O | `backend/cad.py` ezdxf `odafc` | Keep detect paths. Remove embed/mount-ODA-from-our-DMG as a supported distro path. |
| Mutate | `backend/translator.py` ezdxf | Keep extract/write. v0.1: TEXT/MTEXT/ATTRIB/ATTDEF (+ MULTILEADER is already there; keep). Gate DIMENSION/ACAD_TABLE. |
| Queue | `backend/queue.py` | Keep. Serial ODA lock stays. Keys never persisted (`_key` stripped on save). |
| Glossary | `backend/language_assets.py` + `glossaries/*.yaml` | Keep user/project terms. Default UI to zh↔en YAML. |
| MT | DeepL in translator; Azure in `providers/` | Keep as plug-ins. Add OpenAI-compatible in v0.1. User keys only. |
| UI | `frontend/` Vite + React 18 | Rebrand. CN↔EN first. Glossary + keys + ODA status. |
| Pack | PyInstaller specs + `installer/*.iss` / `build_macos.py` | Keep unsigned pack for v0.1. No ODA inside the payload. |
| Update | new `backend/updates.py` + `tools/gen_appcast.py` | GitHub Releases latest. Sparkle appcast + `latest.yml`. In-app **检查更新**. Python fallback opens the asset if Sparkle/WinSparkle is not signed yet. |

Python 3, Node for the frontend build. Frozen app must not require the user to install Python.

`requirements.txt` today: ezdxf, deepl, PyYAML, fastapi, uvicorn, pywebview, python-multipart, cryptography. cryptography is for the licence verifier; drop it from the runtime if licensing.py goes.

---

## DWG kernel / licenses

| Tool | License | Role in the fork |
|---|---|---|
| **ezdxf** | MIT | Working format. Read/modify/write DXF. Already used. |
| **ODA File Converter** | ODA Community Application. Non-members: **non-commercial use only**. Community User Agreement: do not resell or distribute it as part of an application. Drawing SDK is paid membership. | User-installed sidecar only. Same search order Honsen has (`CAD_ODA_EXEC`, system `C:\Program Files\ODA\…`, `/Applications/ODAFileConverter.app`, PATH). **Never copy ODA into NSIS/DMG.** If missing: DXF still works; DWG shows Honsen’s existing “install ODA or Save As DXF” message. |
| **LibreDWG** | GPLv3-or-later | Do not link, do not bundle. |
| **ezdwg** | MIT, write is AC1015 subset | Not in this fork. |
| **ACadSharp** | MIT native DWG | Not in this fork. |

Work DXF version in Honsen is `ACAD2010`. Output DWG version follows the source signature (`AC1012`…`AC1032` → `ACAD13`…`ACAD2018`) or a user pick. Keep that.

macOS ODA is a Qt GUI. Honsen already launches it hidden via `open -g -j -W -n` and stages ASCII temp names because ODA 27.1 breaks on decomposed Unicode filenames. Keep that code.

---

## Entity coverage

Honsen `SUPPORTED_TEXT_TYPES` = TEXT, MTEXT, ATTDEF, ATTRIB, MULTILEADER, DIMENSION, ACAD_TABLE.

**v0.1 (ship / QA):**

| Entity | Action |
|---|---|
| TEXT | Translate `dxf.text`. |
| MTEXT | Translate visible runs. **Keep inline codes** (fork fix vs current wrap-all `\f`). |
| ATTRIB / ATTDEF | Translate value; prompt; tag only when it is not a short code (`MJ01`). Sync ATTRIB tags if ATTDEF tag changes (already in Honsen). |
| INSERT attribs | Always. |
| Title-block text inside block refs | Keep Honsen’s INSERT walk even when “translate all blocks” is off. |
| MULTILEADER | Keep (context mtext / block attributes). Not dims/tables. |
| DIMENSION / ACAD_TABLE | **Off in v0.1 UI and acceptance.** Code may stay behind a flag. |

**v0.2 (enable and test):**

| Entity | Honsen already does | v0.2 work |
|---|---|---|
| DIMENSION | Translate `dxf.text` only if not `""` / `"<>"`. Measurement placeholder stays. | Turn on. Fixture: override 中文 note vs `<>`. |
| ACAD_TABLE | Read/write AcDbTable group-code **302** slots (not only the `*T` preview block). | Turn on. Fixture: cell roundtrip that still holds after AutoCAD rebuilds the table. |
| Full block-def scan | Checkbox `translate_blocks`. | Keep as advanced option. |

Not in v0.1 or v0.2 unless 轻语-parity later: proxy entities, OLE extract, 阵列, 原译对照 / 译原对照 overlays, 50 languages.

轻语 public site (the bar, not the clone list): 单行/多行/块/属性/代理/表格/标注/引线/阵列; R12–R2018; 纯译文 + 对照; MT (百度/有道/腾讯) + LLM (DeepSeek/GPT/混元); 去重与非译过滤; 批量; 不依赖 AutoCAD. Dwglot v0.1 covers the core of that list (text + attribs + CN↔EN + batch + no AutoCAD). Dims/tables in v0.2. The rest is later.

---

## Glossary

Honsen already has three layers. Keep them.

1. Builtin YAML under `glossaries/` (`translation_context_zh_to_en.yaml`, `translation_context_en_to_zh.yaml`). Exact whole-string hit skips MT.
2. Global terms in `~/.cad_translator_language_assets.sqlite3` (user-editable).
3. Project package `.hcterms.json` (portable).

Match order (already in `translate_text`): project/global term (optional layer contains) → builtin glossary → translation memory → MT → post-corrections.

v0.1 UI: import/export CSV or JSON, edit terms, on/off per job. Default direction zh→en / en→zh. French YAML can stay in the tree unused.

Skip rules (keep + align with 轻语’s public filters): empty, numbers/symbols-only, non-source-language (zh_to_* skips no-CJK), high junk-char ratio. Dedup via `translated_cache` per run. Cross-file dedup is a 轻语 feature; v0.1 can keep per-file cache and TM hits.

---

## SHX / `????` / ODA `\M+`

Two different bugs.

**1. Bytes look like `????` after ODA.** Honsen already decodes `\M+5C6BD`-style escapes as GBK (`decode_oda_mbcs_escapes`). Keep that. Also test `$DWGCODEPAGE` / ANSI_936 DXF.

**2. AutoCAD draws `????` because STYLE is `txt.shx` (no CJK glyphs).** Honsen only picks a Windows TTF for wrapping MTEXT (`SimSun` / `Microsoft YaHei` / …) and does not rewrite the STYLE table. That is not enough on macOS or on TEXT/ATTRIB that still reference SHX.

v0.1 STYLE rewrite, when target or new text is CJK:

- If STYLE font is SHX or Big Font, point it at a Unicode TTF.
- Prefer a bundled OFL font (Noto Sans CJK SC or Source Han Sans SC) so macOS does not depend on SimSun.
- Clear Big Font filename.
- Do not clobber a style that already uses a CJK TTF.

Do not ship Autodesk SHX. NOTICE the OFL font.

---

## Layouts vs model space

Keep Honsen:

- Model space always.
- Every paperspace layout (`doc.layouts`).
- Visible text inside INSERT block refs (title frames) even if “translate all blocks” is off.
- Optional full `doc.blocks` scan, including anonymous `*U`. When the checkbox is off, still scan `*T` / `*D` (table/dim preview blocks) so v0.2 tables/dims are not empty.

Frozen / off / locked layers: v0.1 translates them (Honsen does). 轻语 exposes import checkboxes for those; later.

---

## Batch

Keep `backend/queue.py`:

- Add many DXF/DWG files. Start / pause / resume / stop / retry / clear.
- New files only. Prefix `en_` / `zh_` (drop `fr_` as the default). Never overwrite the source.
- Persist `~/.cad_translator_queue.json` (rename to a Dwglot path in v0.1). Restart restores queued work. Running jobs become queued.
- Global max 3 files; per-key semaphore 2; **all ODA conversions serial**.
- Keys live on the in-memory task (`_key`) and are stripped before disk.

v0.1 does not need Excel extract/apply if MT + glossary cover the loop. 轻语’s public “人工翻译” Excel path is a later add if users ask. Do not block v0.1 on it.

---

## MT plug-in

Honsen shape: provider object + user key in `~/.cad_translator_config.json` (or `DEEPL_API_KEY`). Azure region extra field. Local monthly usage counters in SQLite (not telemetry).

v0.1 providers:

- DeepL (already)
- Azure Translator (already)
- **OpenAI-compatible** (new): base URL, key, model. Covers DeepSeek / 通义 / local vLLM, which is the 轻语 public “大模型” slot without us shipping an account.

Interface stays in `backend/providers/`. One function: batch of strings in, strings out. Glossary hits never sent.

Rules:

- User supplies keys. No Dwglot account.
- **No telemetry by default.** Remove licence network time-sync (`TIME_SOURCES` HEAD to microsoft.com / cloudflare) from the default build. Local usage counters may stay. No crash-phone-home.
- Quota errors fail the job (Azure F0 path already stops). No retry storm on 401.

---

## Auto-update (v0.1)

Repo: `https://github.com/erict16/dwglot`. Version lives in `backend/app_meta.py` (`APP_VERSION`).

Check: `GET /api/updates/check` hits GitHub Releases `latest` (no telemetry). UI **检查更新** shows current vs latest and a download link.

Mac: Sparkle appcast (`appcast.xml`) generated by `tools/gen_appcast.py` from a release tag. Embed `Sparkle.framework` in the `.app` when a Developer ID exists. Until then, the Python fallback opens the new DMG/zip from Releases (user installs). Unsigned Sparkle will not satisfy Gatekeeper.

Win: WinSparkle next to the EXE, same Releases + `latest.yml`. Same fallback: open the new installer from Releases until Authenticode exists.

Do not phone home except this user-triggered (or explicit in-app) Releases query. Do not switch the shell to Tauri/Electron for an updater.

---

## Installer / signing (v0.2)

Honsen already has:

- Windows: `Honsen_CAD_Translator_v1.8.8.spec` + Inno Setup `installer/Honsen_DrawTranslate_Setup.iss` + `build_installer.ps1`
- macOS: `Honsen_CAD_Translator_v1.8.8_macos.spec` + `installer/build_macos.py` (arm64 vs Intel, optional `--identity`, notarize)

v0.1: produce those artifacts **unsigned**, **without ODA inside**. Document SmartScreen / Gatekeeper. Point the ODA setting at the official guestfiles download.

v0.2: Authenticode (Win x64), Developer ID + notarize (darwin-x86_64 and darwin-aarch64 separately). Two Mac builds, no fake universal from Homebrew arm64 Python.

Windows 10/11 needs WebView2 (Win11 usually has it). macOS 11+.

---

## Repo layout (after fork)

Keep Honsen’s tree. Rename brands and config filenames.

```
dwglot/                          # fork of etianwang/CAD_translator
  PLAN.md
  LICENSE                        # MIT, Honsen + Dwglot
  NOTICE                         # ezdxf, OFL font
  run.py
  backend/                       # cad, translator, queue, api, storage, providers
  desktop/                       # pywebview launcher
  frontend/                      # Vite React
  glossaries/                    # zh-en YAML first
  fonts/                         # Noto/Source Han SC (new)
  installer/                     # Inno + macOS build; no ODA payload
  tests/
  docs/oda.md                    # user-install ODA, licence warning
```

Delete or quarantine from the default product: `tools/` keygen, `license_public_key.txt`, payment QR URLs, Honsen WeChat. Leave `LICENSE_ENFORCEMENT_ENABLED` false until the files are gone.

Config paths to rename: `~/.cad_translator_config.json`, `~/.cad_translator_queue.json`, `~/.cad_translator_language_assets.sqlite3` → Dwglot equivalents. Optional one-time migrate from Honsen names.

---

## v0.1 vs v0.2

**v0.1**

1. Fork Honsen into `/workspace/dwglot` (or GitHub `dwglot`). MIT.
2. Rebrand 图译 / Dwglot. Default zh→en / en→zh.
3. Strip licence/payment/keygen from the product path.
4. ODA: detect only. Rewrite README/installer so they never copy ODA into dist.
5. TEXT / MTEXT (codes kept) / ATTRIB / ATTDEF. Model + paperspace + title-block INSERTs. Batch new files.
6. User glossary + DeepL / Azure / OpenAI-compatible keys. No telemetry.
7. STYLE SHX → bundled Unicode TTF. Keep `\M+` GBK decode.
8. Gate DIMENSION / ACAD_TABLE off in the UI.
9. Auto-update: GitHub Releases + Sparkle/WinSparkle (unsigned OK) + Python fallback. In-app 检查更新.
10. Unsigned PyInstaller + Inno/DMG without ODA.
11. Tests on synthetic DXF (no ODA in CI).
12. Mac UI from `sketches/01.html` (traffic lights, unified toolbar, sidebar, 原文|译文 table). 批量导出 and 参数 sheet as pages.

**v0.2**

1. Enable and fixture DIMENSION overrides and ACAD_TABLE 302 write.
2. Signed Win + two Mac installers so Sparkle/WinSparkle and Gatekeeper/SmartScreen are quiet.
3. Nice-to-have from 轻语’s public list: Excel human path, 对照 overlay, more MT brands.

---

## File-level fork steps (when implementing)

Do not do this in the plan turn.

1. Clone Honsen; commit as the Dwglot baseline. Keep history.
2. Rebrand strings: `desktop/launcher.py`, frontend, mutex, output prefixes, config paths.
3. Remove licensing UI and `tools/`. Stop network time check.
4. `installer/*` and README: delete ODA-embed instructions; keep path detect in `backend/cad.py`; drop `_mount_embedded_macos_odafc` as a release feature (or leave code but never put a DMG in Resources).
5. `backend/translator.py`: preserve MTEXT codes; add STYLE rewrite; skip DIMENSION/ACAD_TABLE unless `enable_v02_entities`.
6. `backend/providers/openai_compat.py` + settings fields.
7. Frontend: CN↔EN, glossary editor, ODA status, keys.
8. `tests/`: TEXT/MTEXT/ATTRIB roundtrip DXF; SHX style rewrite; glossary hit does not call MT; ODA-missing DWG error text.
9. Pack unsigned Win/Mac.

---

## Risks

1. **ODA licence.** Forking Honsen includes their “put ODA next to the exe” habit. Shipping that folder is the failure mode. Detect + link official download only.
2. **MTEXT fidelity.** Current write wraps everything in `\f`. Easy to ship v0.1 that looks translated in Notepad and broken in AutoCAD. Preserve codes.
3. **SHX `????`.** Without STYLE rewrite, CN output is unreadable. This is v0.1, not polish.
4. **Dims/tables already in the file.** Leaving them on “because Honsen did” blows the v0.1 bar. Gate them.
5. **ACAD_TABLE 302 write** may not survive every AutoCAD rebuild. That is why it is v0.2 with a real fixture.
6. **Licence leftover.** Even disabled, `licensing.py` phones the network for time when enabled. Delete from the product path so a flag flip cannot come back.
7. **PyInstaller + three arches.** Honsen already warns Homebrew Python is arm64-only. v0.1 unsigned; v0.2 needs real Intel and Apple Silicon machines (or GH `macos-latest` + `--target`).
8. **WebView2 on Win10.** Call it out in README.
9. **No public DWG in CI.** DXF fixtures only until we draw our own DWG.
10. **轻语 feature envy.** Public site lists 50 langs, 对照, OLE, proxy, 智能排版. Those are not v0.1. Do not decompile 轻语 to chase them.

---

## Verify (when built)

- Fork runs as 图译. No Honsen title, no activate-code screen, no ODA inside the installer payload.
- DXF TEXT / MTEXT-with-`{\\C1;红}` / ATTRIB roundtrip, zh↔en, new file next to source.
- Glossary exact hit never hits the fake MT.
- SHX style on a CJK target points at the bundled TTF.
- DWG without ODA: clear message. DWG with user ODA: one self-made file opens after translate.
- Queue restart does not write API keys to disk.
- DIMENSION `<>` and ACAD_TABLE are not translated in v0.1.

No app scaffold this turn. Local `PLAN.md` only.
