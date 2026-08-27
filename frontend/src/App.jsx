import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import "./App.css";

const LANGS = [
  ["zh-Hans", "中"],
  ["en", "英"],
  ["ja", "日"],
  ["ko", "韩"],
  ["de", "德"],
  ["fr", "法"],
];

function py() {
  return window.pywebview?.api || null;
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const text = await response.text();
  let data = {};
  try {
    data = text ? JSON.parse(text) : {};
  } catch {
    data = { detail: text };
  }
  if (!response.ok) {
    const detail = data.detail || data.message || `HTTP ${response.status}`;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return data;
}

function asText(value) {
  if (value == null) return "";
  return typeof value === "string" ? value : String(value);
}

function asCount(value) {
  const n = Number(value);
  return Number.isFinite(n) ? n : 0;
}

function modeKey(source, target) {
  source = asText(source);
  target = asText(target);
  if (source === "zh-Hans" && target === "en") return "zh_to_en";
  if (source === "en" && target === "zh-Hans") return "en_to_zh";
  if (source === "zh-Hans" && target === "fr") return "zh_to_fr";
  if (source === "fr" && target === "zh-Hans") return "fr_to_zh";
  return `${source}_to_${target}`;
}

function engineProvider(engine, config) {
  if (engine === "local") return "ollama";
  if (engine === "custom") return "openai";
  return asText(config?.provider) === "azure" ? "azure" : "deepl";
}

function enginePayload(engine, config) {
  config = config && typeof config === "object" ? config : {};
  return {
    provider: engineProvider(engine, config),
    deepl_key: asText(config.deepl_key),
    azure_key: asText(config.azure_key),
    azure_region: asText(config.azure_region),
    openai_key: asText(config.openai_key),
    openai_base: asText(config.openai_base),
    openai_model: asText(config.openai_model),
    ollama_host: asText(config.ollama_host),
    ollama_model: asText(config.ollama_model),
    project_package_path: asText(config.project_package_path),
  };
}

function asFiles(paths) {
  return (paths || []).map((path) => ({
    path,
    name: String(path).split(/[/\\]/).pop(),
    ext: String(path).toLowerCase().endsWith(".dxf") ? "DXF" : "DWG",
  }));
}

export default function App() {
  const [tab, setTab] = useState("regular");
  const [sheet, setSheet] = useState(false);
  const [files, setFiles] = useState([]);
  const [current, setCurrent] = useState("");
  const [rows, setRows] = useState([]);
  const [engine, setEngine] = useState("cloud");
  const [sourceLang, setSourceLang] = useState("zh-Hans");
  const [targetLang, setTargetLang] = useState("en");
  const [layout, setLayout] = useState("纯译文");
  const [filters, setFilters] = useState({ numbers: true, dupes: true, nonsource: true });
  const [params, setParams] = useState({
    attribs: true,
    dims: true,
    model: true,
    paper: true,
    frozen: false,
    locked: false,
    off: false,
  });
  const [oda, setOda] = useState({ installed: false, path: "" });
  const [glossary, setGlossary] = useState(0);
  const [status, setStatus] = useState("放入 DWG / DXF，提取文字后再译。");
  const [config, setConfig] = useState({
    provider: "deepl",
    deepl_key: "",
    azure_key: "",
    azure_region: "",
    output_dir: "",
    openai_key: "",
    openai_base: "",
    openai_model: "",
    ollama_host: "",
    ollama_model: "",
    project_package_path: "",
  });
  const [batch, setBatch] = useState({ tasks: [], running: false });
  const [updateMsg, setUpdateMsg] = useState("");
  const [busy, setBusy] = useState(false);
  const [lastOutput, setLastOutput] = useState("");
  const [writtenPath, setWrittenPath] = useState("");
  const cadInput = useRef(null);
  const glossaryInput = useRef(null);

  useEffect(() => {
    const shot = new URLSearchParams(window.location.search).get("shot");
    if (!shot) return;
    setFiles([
      { path: "/demo/电气原理图.dwg", name: "电气原理图.dwg", ext: "DWG" },
      { path: "/demo/配电箱.dxf", name: "配电箱.dxf", ext: "DXF" },
      { path: "/demo/总图-A1.dwg", name: "总图-A1.dwg", ext: "DWG" },
    ]);
    setCurrent("/demo/电气原理图.dwg");
    setRows([
      { id: 0, source: "电气原理图", target: "Electrical Schematic", layer: "0", type: "MTEXT", duplicate: false },
      { id: 1, source: "平面布置图", target: "Floor Plan", layer: "0", type: "TEXT", duplicate: false },
      { id: 2, source: "隔墙定位图", target: "Partition Location Plan", layer: "A-WALL", type: "TEXT", duplicate: false },
      { id: 3, source: "进线柜", target: "Incoming Cabinet", layer: "E-POWR", type: "ATTRIB", duplicate: false },
      { id: 4, source: "接地", target: "Earthing", layer: "E-POWR", type: "MTEXT", duplicate: false },
      { id: 5, source: "总图", target: "General Layout", layer: "TITLE", type: "TEXT", duplicate: false },
      { id: 6, source: "材料表", target: "Bill of Materials", layer: "0", type: "TABLE", duplicate: false },
      { id: 7, source: "电缆桥架", target: "Cable Tray", layer: "E-TRAY", type: "MTEXT", duplicate: false },
    ]);
    if (shot === "export") setTab("export");
    if (shot === "params") {
      setTab("regular");
      setSheet(true);
    }
  }, []);

  const visibleRows = useMemo(() => {
    const list = Array.isArray(rows) ? rows : [];
    const source = asText(sourceLang);
    const sourceIsZh = source.startsWith("zh");
    const sourceIsAscii = source === "en" || source === "de" || source === "fr";
    return list.filter((row) => {
      const source = asText(row?.source);
      if (filters.dupes && row?.duplicate) return false;
      if (filters.numbers && source && /^[\d.\-\s]+$/.test(source)) return false;
      if (filters.nonsource) {
        const hasCjk = /[\u4e00-\u9fff]/.test(source);
        if (sourceIsZh && !hasCjk) return false;
        if (sourceIsAscii && hasCjk) return false;
      }
      return true;
    });
  }, [rows, filters, sourceLang]);

  const selected = files.find((item) => item.path === current);

  const refreshMeta = useCallback(async () => {
    try {
      const [odaStatus, assets, cfg] = await Promise.all([
        api("/api/odafc-status"),
        api("/api/language-assets"),
        api("/api/config"),
      ]);
      setOda(odaStatus);
      setGlossary(asCount(assets.builtin_terms?.length) + asCount(assets.terms?.length));
      setConfig(cfg && typeof cfg === "object" && !Array.isArray(cfg) ? cfg : {});
    } catch (error) {
      setStatus(error.message);
    }
  }, []);

  useEffect(() => {
    refreshMeta();
  }, [refreshMeta]);

  useEffect(() => {
    if (tab !== "export") return undefined;
    const timer = setInterval(() => {
      api("/api/batch").then(setBatch).catch(() => {});
    }, 1200);
    api("/api/batch").then(setBatch).catch(() => {});
    return () => clearInterval(timer);
  }, [tab]);

  async function loadOpened(next) {
    if (!next.length) {
      setStatus("没有选择文件。");
      return;
    }
    setFiles(next);
    setWrittenPath("");
    setCurrent(next[0].path);
    await extractFile(next[0].path);
  }

  async function openDrawings() {
    const native = py();
    if (native?.pick_cad_files) {
      const picked = await native.pick_cad_files();
      const paths = picked?.paths || [];
      if (!paths.length) {
        setStatus("没有选择文件。");
        return;
      }
      await loadOpened(asFiles(paths));
      return;
    }
    cadInput.current?.click();
  }

  async function onCadPicked(event) {
    const picked = [...(event.target.files || [])];
    event.target.value = "";
    if (!picked.length) return;
    const form = new FormData();
    picked.forEach((file) => form.append("files", file));
    setBusy(true);
    setStatus("正在打开图纸…");
    try {
      const response = await fetch("/api/drawings/open", { method: "POST", body: form });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "打开失败");
      await loadOpened(data.files || []);
    } catch (error) {
      setStatus(error.message);
    } finally {
      setBusy(false);
    }
  }

  async function extractFile(path) {
    setBusy(true);
    setStatus("正在提取文字…");
    try {
      const data = await api("/api/drawings/extract", {
        method: "POST",
        body: JSON.stringify({
          path,
          include_blocks: false,
          include_attribs: params.attribs,
          include_model: params.model,
          include_paper: params.paper,
          include_frozen: params.frozen,
          include_locked: params.locked,
          include_off: params.off,
          enable_v02: params.dims,
          skip_numbers: filters.numbers,
          skip_dupes: filters.dupes,
          skip_nonsource: filters.nonsource,
          translation_mode: modeKey(sourceLang, targetLang),
        }),
      });
      setRows(Array.isArray(data.items) ? data.items : []);
      setStatus(`提取 ${asCount(data.count)} 条，去重后 ${asCount(data.unique)}。`);
    } catch (error) {
      setRows([]);
      setStatus(error.message);
    } finally {
      setBusy(false);
    }
  }

  function closeSheet() {
    setSheet(false);
    if (current) extractFile(current);
  }

  async function runTranslate() {
    if (!current) {
      setStatus("先打开图纸。");
      return;
    }
    if (!rows.length) {
      setStatus("这张图没有可译文字。");
      return;
    }
    setBusy(true);
    setStatus("正在翻译…");
    try {
      const data = await api("/api/drawings/translate", {
        method: "POST",
        body: JSON.stringify({
          items: rows,
          translation_mode: modeKey(sourceLang, targetLang),
          ...enginePayload(engine, config),
        }),
      });
      setRows(Array.isArray(data.items) ? data.items : []);
      const bits = [`术语 ${asCount(data.glossary)}`, `引擎 ${asCount(data.mt)}`];
      if (asCount(data.skipped)) bits.push(`未译 ${asCount(data.skipped)}`);
      if (data.skipped && !data.has_engine) {
        const hint = engine === "local"
          ? "请先启动 Ollama。"
          : engine === "custom"
            ? "无法连接自定义接口。"
            : "剩下的要填云引擎 Key，或手填译文。";
        setStatus(`${bits.join("，")}。${hint}`);
        if (engine !== "local") setSheet(true);
      } else {
        setStatus(`译完。${bits.join("，")}。可以改译文再写回。`);
      }
    } catch (error) {
      setStatus(error.message);
    } finally {
      setBusy(false);
    }
  }

  async function writeBack() {
    if (!current) {
      setStatus("先打开图纸。");
      return;
    }
    const ready = visibleRows.filter((row) => row.selected !== false && asText(row.target).trim());
    if (!ready.length) {
      setStatus("没有可写回的译文。先点翻译，或手填译文。");
      return;
    }
    setBusy(true);
    setStatus("正在写回图纸…");
    try {
      const named = await api(
        `/api/default-output-name?mode=${encodeURIComponent(modeKey(sourceLang, targetLang))}&base=${encodeURIComponent(selected?.name?.replace(/\.[^.]+$/, "") || "drawing")}`
      );
      const data = await api("/api/drawings/writeback", {
        method: "POST",
        body: JSON.stringify({
          input_file: current,
          items: visibleRows,
          output_dir: config.output_dir,
          output_name: named.name,
          translation_mode: modeKey(sourceLang, targetLang),
          style: layout,
        }),
      });
      setWrittenPath(data.path || "");
      setLastOutput(data.path || "");
      setStatus(`已写回 ${data.written} 条（${layout}）→ ${data.path}`);
      const native = py();
      if (native?.reveal_file && data.path) native.reveal_file(data.path);
    } catch (error) {
      setStatus(error.message);
    } finally {
      setBusy(false);
    }
  }

  function cadPathForPdf() {
    const written = asText(writtenPath);
    if (written && /\.(dxf|dwg)$/i.test(written)) return written;
    return current;
  }

  async function exportPdf(andPrint = false) {
    if (!current) {
      setStatus("先打开图纸。");
      return;
    }
    const sourcePath = cadPathForPdf();
    const fromWriteback = Boolean(writtenPath) && sourcePath === writtenPath;
    const stem = (fromWriteback ? sourcePath : selected?.name || "drawing")
      .replace(/^.*[/\\]/, "")
      .replace(/\.[^.]+$/, "") || "drawing";
    setBusy(true);
    setStatus(andPrint ? "正在导出并打印…" : "正在导出 PDF…");
    try {
      const data = await api(andPrint ? "/api/drawings/print" : "/api/drawings/export-pdf", {
        method: "POST",
        body: JSON.stringify({
          path: sourcePath,
          output_dir: config.output_dir,
          output_name: `${stem}.pdf`,
          style: "纯译文",
          items: [],
        }),
      });
      setLastOutput(data.path || "");
      if (andPrint) {
        const printed = data.print || {};
        const where = fromWriteback ? "写回图纸" : "原图，还未写回译文";
        setStatus(printed.ok ? `已送到系统打印（${where}）：${data.path}` : `${printed.message || data.message || "打印没发出去"}。PDF：${data.path}`);
        const native = py();
        if (native?.print_pdf && data.path && !printed.ok) native.print_pdf(data.path);
      } else if (fromWriteback) {
        setStatus(`PDF 已导出（写回图纸）→ ${data.path}`);
        const native = py();
        if (native?.reveal_file && data.path) native.reveal_file(data.path);
      } else {
        setStatus(`PDF 已导出（原图，还未写回译文。ezdxf drawing，不是 AutoCAD 出图）→ ${data.path}`);
        const native = py();
        if (native?.reveal_file && data.path) native.reveal_file(data.path);
      }
    } catch (error) {
      setStatus(error.message);
    } finally {
      setBusy(false);
    }
  }

  async function startExport() {
    const paths = files.map((item) => item.path);
    if (!paths.length) {
      setStatus("先打开图纸。");
      return;
    }
    try {
      await api("/api/batch/add", { method: "POST", body: JSON.stringify({ files: paths }) });
      const started = await api("/api/batch/start", {
        method: "POST",
        body: JSON.stringify({
          output_dir: config.output_dir,
          translation_mode: modeKey(sourceLang, targetLang),
          translate_blocks: false,
          include_attribs: params.attribs,
          enable_v02: params.dims,
          include_model: params.model,
          include_paper: params.paper,
          include_frozen: params.frozen,
          include_locked: params.locked,
          include_off: params.off,
          skip_numbers: filters.numbers,
          skip_dupes: filters.dupes,
          skip_nonsource: filters.nonsource,
          output_format: "source",
          style: layout,
          ...enginePayload(engine, config),
        }),
      });
      setTab("export");
      setStatus(started.message || "批量导出已开始。");
    } catch (error) {
      setStatus(error.message);
    }
  }

  async function checkUpdates() {
    setUpdateMsg("正在检查…");
    try {
      const data = await api("/api/updates/check");
      if (data.available) {
        setUpdateMsg(`有新版本 ${data.latest}（当前 ${data.current}）`);
        const native = py();
        if (native?.open_url && data.html_url) native.open_url(data.html_url);
        else if (data.html_url) window.open(data.html_url, "_blank");
      } else {
        setUpdateMsg(data.message || `已是 ${data.current}`);
      }
    } catch {
      setUpdateMsg("GitHub API 暂不可用，打开 Releases 页查看");
    }
  }

  async function loadGlossary() {
    const native = py();
    if (native?.pick_term_package) {
      const picked = await native.pick_term_package();
      if (picked?.path) {
        try {
          await api("/api/language-assets/project", {
            method: "POST",
            body: JSON.stringify({ path: picked.path, create: false }),
          });
          await refreshMeta();
          setStatus("已加载术语表。");
        } catch (error) {
          setStatus(error.message);
        }
        return;
      }
    }
    glossaryInput.current?.click();
  }

  async function onGlossaryPicked(event) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    try {
      const text = await file.text();
      if (!text.trim()) {
        setStatus("术语表是空的");
        return;
      }
      const mode = modeKey(sourceLang, targetLang);
      let payload = { mode, csv: "", terms: [] };
      if (/\.json$/i.test(file.name)) {
        const data = JSON.parse(text);
        payload.terms = data.terms || [];
      } else {
        payload.csv = text;
      }
      const result = await api("/api/language-assets/import", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      await refreshMeta();
      setStatus(`术语表写入 ${result.count} 条。`);
    } catch (error) {
      const message = String(error.message || "");
      setStatus(/json|unexpected/i.test(message) ? "术语表读不出来。" : message || "术语表读不出来。");
    }
  }

  function onLights(kind) {
    const native = py();
    if (kind === "close") native?.close_window?.();
    if (kind === "min") native?.minimize_window?.();
    if (kind === "max") native?.toggle_maximize?.();
  }

  return (
    <div className="win" data-theme="light">
      <header className="tb pywebview-drag-region">
        <div className="lights" aria-hidden="true">
          <i className="r" onClick={() => onLights("close")} />
          <i className="y" onClick={() => onLights("min")} />
          <i className="g" onClick={() => onLights("max")} />
        </div>
        <div className="brand">图译</div>
        <div className="seg" role="tablist">
          <button type="button" className={tab === "regular" ? "on" : ""} onClick={() => setTab("regular")}>常规处理</button>
          <button type="button" className={tab === "export" ? "on" : ""} onClick={() => setTab("export")}>批量导出</button>
          <button type="button" className={tab === "import" ? "on" : ""} onClick={() => setTab("import")}>批量导入</button>
        </div>
        <div className="pair">
          <select aria-label="源语言" value={sourceLang} onChange={(event) => setSourceLang(event.target.value)}>
            {LANGS.map(([code, label]) => <option key={code} value={code}>{label}</option>)}
          </select>
          →
          <select aria-label="目标语言" value={targetLang} onChange={(event) => setTargetLang(event.target.value)}>
            {LANGS.map(([code, label]) => <option key={code} value={code}>{label}</option>)}
          </select>
        </div>
        <div className="pill" role="radiogroup" aria-label="引擎">
          {[["cloud", "云"], ["local", "本地"], ["custom", "自定义"]].map(([value, label]) => (
            <label key={value}>
              <input type="radio" name="eng" checked={engine === value} onChange={() => setEngine(value)} />
              {label}
            </label>
          ))}
        </div>
        <span className="grow" />
        <button type="button" className="tbtn" onClick={openDrawings}>打开图纸</button>
        <button type="button" className="tbtn" onClick={loadGlossary}>加载术语表</button>
        <button type="button" className={`tbtn${sheet ? " pri" : ""}`} onClick={() => setSheet(true)}>参数</button>
        {tab === "export" ? (
          <button type="button" className="tbtn pri" disabled={busy} onClick={startExport}>开始导出</button>
        ) : tab === "regular" ? (
          <>
            <button type="button" className="tbtn pri" disabled={busy} onClick={runTranslate}>翻译</button>
            <button type="button" className="tbtn" disabled={busy} onClick={writeBack}>写回</button>
            <button type="button" className="tbtn" disabled={busy || !current} onClick={() => exportPdf(false)}>导出 PDF</button>
            <button type="button" className="tbtn" disabled={busy || !current} onClick={() => exportPdf(true)}>打印</button>
          </>
        ) : null}
      </header>

      <input ref={cadInput} type="file" accept=".dxf,.dwg,application/dxf" multiple hidden onChange={onCadPicked} />
      <input ref={glossaryInput} type="file" accept=".json,.csv,.txt,.hcterms.json" hidden onChange={onGlossaryPicked} />

      <div className="body">
        <aside
          className="side"
          onDragOver={(event) => event.preventDefault()}
          onDrop={async (event) => {
            event.preventDefault();
            const dropped = [...event.dataTransfer.files].filter((file) => /\.(dxf|dwg)$/i.test(file.name));
            if (!dropped.length) return;
            const form = new FormData();
            dropped.forEach((file) => form.append("files", file));
            try {
              const response = await fetch("/api/drawings/open", { method: "POST", body: form });
              const data = await response.json();
              if (!response.ok) throw new Error(data.detail || "打开失败");
              await loadOpened(data.files || []);
            } catch (error) {
              setStatus(error.message);
            }
          }}
        >
          <h2>{tab === "export" ? `待导出 · ${files.length}` : "已打开"}</h2>
          {files.map((file) => (
            <div
              key={file.path}
              className={`item${file.path === current ? " on" : ""}`}
              onClick={() => {
                if (file.path !== current) setWrittenPath("");
                setCurrent(file.path);
                if (tab === "regular") extractFile(file.path);
              }}
            >
              <span className={`dot${file.ext === "DXF" ? " dxf" : ""}`} />
              {file.name}
              <span className="meta">{file.path === current ? "当前" : file.ext}</span>
            </div>
          ))}
          <p className="hint">{tab === "export" ? "批量时全部去重，并还原目录结构。" : "点工具栏「打开图纸」，或先提取再译。"}</p>
        </aside>

        {tab === "regular" && (
          <section className="main">
            <div className="filters">
              过滤
              <label><input type="checkbox" checked={filters.numbers} onChange={(event) => setFilters((prev) => ({ ...prev, numbers: event.target.checked }))} /> 纯数字</label>
              <label><input type="checkbox" checked={filters.dupes} onChange={(event) => setFilters((prev) => ({ ...prev, dupes: event.target.checked }))} /> 重复</label>
              <label><input type="checkbox" checked={filters.nonsource} onChange={(event) => setFilters((prev) => ({ ...prev, nonsource: event.target.checked }))} /> 非源语言</label>
              <span style={{ marginLeft: "auto" }}>
                版式
                <select value={layout} onChange={(event) => setLayout(event.target.value)} aria-label="导出版式" style={{ height: 24, border: 0, background: "rgba(118,118,128,.12)", borderRadius: 6, padding: "0 8px", font: "600 12px -apple-system,system-ui,sans-serif", color: "inherit", marginLeft: 6 }}>
                  <option>纯译文</option>
                  <option>原译对照</option>
                  <option>译原对照</option>
                </select>
              </span>
            </div>
            <div className="table">
              <table>
                <thead>
                  <tr>
                    <th style={{ width: 36 }}>
                      <input
                        type="checkbox"
                        checked={visibleRows.length > 0 && visibleRows.every((row) => row.selected !== false)}
                        onChange={(event) => {
                          const checked = event.target.checked;
                          const visible = new Set(visibleRows.map((row) => row.id));
                          setRows((prev) => prev.map((item) => (visible.has(item.id) ? { ...item, selected: checked } : item)));
                        }}
                        aria-label="全选"
                      />
                    </th>
                    <th>原文</th>
                    <th>译文</th>
                    <th>图层 / 类型</th>
                  </tr>
                </thead>
                <tbody>
                  {visibleRows.length === 0 ? (
                    <tr>
                      <td colSpan={4} className="kind">{rows.length ? "过滤后没有可显示的文字。" : "这张图没有可译文字。"}</td>
                    </tr>
                  ) : visibleRows.map((row, index) => (
                    <tr key={row.id} className={index === 0 ? "on" : row.duplicate ? "skip" : ""}>
                      <td>
                        <input
                          type="checkbox"
                          checked={row.selected !== false}
                          onChange={(event) => {
                            const checked = event.target.checked;
                            setRows((prev) => prev.map((item) => (item.id === row.id ? { ...item, selected: checked } : item)));
                          }}
                        />
                      </td>
                      <td className="src">{asText(row.source)}</td>
                      <td>
                        <input
                          value={asText(row.target)}
                          onChange={(event) => {
                            const value = event.target.value;
                            setRows((prev) => prev.map((item) => (item.id === row.id ? { ...item, target: value, via: "edit" } : item)));
                          }}
                          style={{ width: "100%", border: 0, background: "transparent", color: "inherit", font: "inherit" }}
                        />
                      </td>
                      <td className="kind">{asText(row.layer) || "0"} · {asText(row.type)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        )}

        {tab === "export" && (
          <>
            <div className="jobs">
              {(batch.tasks || []).length === 0 && files.map((file) => (
                <div className="job" key={file.path}>
                  <span>{file.name}</span>
                  <span>{layout}</span>
                  <span className="bar"><i style={{ width: 0 }} /></span>
                  <span>待导出</span>
                </div>
              ))}
              {(batch.tasks || []).map((task) => (
                <div className="job" key={task.id}>
                  <span>{(task.input_file || "").split(/[/\\]/).pop()}</span>
                  <span>{layout}</span>
                  <span className="bar"><i style={{ width: `${task.progress || 0}%` }} /></span>
                  <span>{task.status}</span>
                </div>
              ))}
            </div>
            <aside className="insp">
              <h3>导出版式</h3>
              {["纯译文", "原译对照", "译原对照"].map((name) => (
                <label className="row" key={name}>
                  <input type="radio" name="lay" checked={layout === name} onChange={() => setLayout(name)} /> {name}
                </label>
              ))}
              <h3>输出位置</h3>
              <div className="path">
                <input value={config.output_dir || ""} readOnly />
                <button type="button" onClick={async () => {
                  const picked = await py()?.pick_output_dir?.();
                  if (picked?.path) {
                    await api("/api/config", { method: "POST", body: JSON.stringify({ ...config, output_dir: picked.path }) });
                    setConfig((prev) => ({ ...prev, output_dir: picked.path }));
                  }
                }}>选取</button>
              </div>
              <h3>过滤</h3>
              <label className="row"><input type="checkbox" checked={filters.numbers} onChange={(event) => setFilters((prev) => ({ ...prev, numbers: event.target.checked }))} /> 纯数字</label>
              <label className="row"><input type="checkbox" checked={filters.dupes} onChange={(event) => setFilters((prev) => ({ ...prev, dupes: event.target.checked }))} /> 重复</label>
              <label className="row"><input type="checkbox" checked={filters.nonsource} onChange={(event) => setFilters((prev) => ({ ...prev, nonsource: event.target.checked }))} /> 非源语言</label>
              <h3>PDF</h3>
              <button type="button" className="tbtn" disabled={busy || !current} onClick={() => exportPdf(false)}>导出 PDF</button>
              <button type="button" className="tbtn" disabled={busy || !current} onClick={() => exportPdf(true)}>打印</button>
              {lastOutput && <p className="note">{lastOutput}</p>}
            </aside>
          </>
        )}

        {tab === "import" && (
          <section className="main">
            <div className="filters">批量导入还没做。</div>
            <div className="table" style={{ padding: 24, color: "var(--muted)" }}>
              表格回填以后再说。现在请用「常规处理」里的写回，或去「批量导出」。
            </div>
          </section>
        )}

        {sheet && (
          <>
            <div className="dim" onClick={closeSheet} />
            <div className="sheet" role="dialog" aria-label="参数">
              <div className="sheet-h">
                <span>参数</span>
                <button type="button" className="done" onClick={closeSheet}>完成</button>
              </div>
              <div className="sheet-b">
                <div className="group">
                  <h4>导入范围</h4>
                  <label><input type="checkbox" checked={params.attribs} onChange={(event) => setParams((prev) => ({ ...prev, attribs: event.target.checked }))} /> 块属性</label>
                  <label><input type="checkbox" checked={params.dims} onChange={(event) => setParams((prev) => ({ ...prev, dims: event.target.checked }))} /> 标注、表格</label>
                  <label><input type="checkbox" checked={params.model} onChange={(event) => setParams((prev) => ({ ...prev, model: event.target.checked }))} /> 模型空间</label>
                  <label><input type="checkbox" checked={params.paper} onChange={(event) => setParams((prev) => ({ ...prev, paper: event.target.checked }))} /> 图纸空间</label>
                </div>
                <div className="group">
                  <h4>图层</h4>
                  <label><input type="checkbox" checked={params.frozen} onChange={(event) => setParams((prev) => ({ ...prev, frozen: event.target.checked }))} /> 冻结图层中的文字</label>
                  <label><input type="checkbox" checked={params.locked} onChange={(event) => setParams((prev) => ({ ...prev, locked: event.target.checked }))} /> 锁定图层中的文字</label>
                  <label><input type="checkbox" checked={params.off} onChange={(event) => setParams((prev) => ({ ...prev, off: event.target.checked }))} /> 关闭图层中的文字</label>
                </div>
                <div className="group">
                  <h4>过滤</h4>
                  <label><input type="checkbox" checked={filters.numbers} onChange={(event) => setFilters((prev) => ({ ...prev, numbers: event.target.checked }))} /> 纯数字、符号</label>
                  <label><input type="checkbox" checked={filters.dupes} onChange={(event) => setFilters((prev) => ({ ...prev, dupes: event.target.checked }))} /> 重复内容</label>
                  <label><input type="checkbox" checked={filters.nonsource} onChange={(event) => setFilters((prev) => ({ ...prev, nonsource: event.target.checked }))} /> 非源语言</label>
                </div>
                <div className="group">
                  <h4>导出版式</h4>
                  {["纯译文", "原译对照", "译原对照"].map((name) => (
                    <label key={name}><input type="radio" name="sheet-lay" checked={layout === name} onChange={() => setLayout(name)} /> {name}</label>
                  ))}
                </div>
                <div className="group">
                  <h4>引擎 · 语言</h4>
                  <label><input type="radio" name="sheet-eng" checked={engine === "cloud"} onChange={() => setEngine("cloud")} /> 云</label>
                  <label><input type="radio" name="sheet-eng" checked={engine === "local"} onChange={() => setEngine("local")} /> 本地</label>
                  <label><input type="radio" name="sheet-eng" checked={engine === "custom"} onChange={() => setEngine("custom")} /> 自定义</label>
                  <p className="note">语言看工具栏。云 = DeepL / Azure；本地 = Ollama；自定义 = OpenAI 兼容接口。</p>
                  {engine === "cloud" && (
                    <>
                      <label>云服务
                        <select value={config.provider === "azure" ? "azure" : "deepl"} onChange={(event) => setConfig((prev) => ({ ...prev, provider: event.target.value }))} style={{ marginLeft: 8 }}>
                          <option value="deepl">DeepL</option>
                          <option value="azure">Azure</option>
                        </select>
                      </label>
                      <label>DeepL <input value={config.deepl_key || ""} onChange={(event) => setConfig((prev) => ({ ...prev, deepl_key: event.target.value }))} style={{ marginLeft: 8, flex: 1 }} /></label>
                      <label>Azure <input value={config.azure_key || ""} onChange={(event) => setConfig((prev) => ({ ...prev, azure_key: event.target.value }))} style={{ marginLeft: 8, flex: 1 }} /></label>
                      <label>Region <input value={config.azure_region || ""} onChange={(event) => setConfig((prev) => ({ ...prev, azure_region: event.target.value }))} style={{ marginLeft: 8, flex: 1 }} /></label>
                    </>
                  )}
                  {engine === "local" && (
                    <>
                      <label>Ollama <input value={config.ollama_host || ""} placeholder="http://127.0.0.1:11434" onChange={(event) => setConfig((prev) => ({ ...prev, ollama_host: event.target.value }))} style={{ marginLeft: 8, flex: 1 }} /></label>
                      <label>模型 <input value={config.ollama_model || ""} placeholder="llama3.1" onChange={(event) => setConfig((prev) => ({ ...prev, ollama_model: event.target.value }))} style={{ marginLeft: 8, flex: 1 }} /></label>
                    </>
                  )}
                  {engine === "custom" && (
                    <>
                      <label>Key <input value={config.openai_key || ""} onChange={(event) => setConfig((prev) => ({ ...prev, openai_key: event.target.value }))} style={{ marginLeft: 8, flex: 1 }} /></label>
                      <label>URL <input value={config.openai_base || ""} placeholder="https://api.deepseek.com/v1" onChange={(event) => setConfig((prev) => ({ ...prev, openai_base: event.target.value }))} style={{ marginLeft: 8, flex: 1 }} /></label>
                      <label>模型 <input value={config.openai_model || ""} placeholder="deepseek-chat" onChange={(event) => setConfig((prev) => ({ ...prev, openai_model: event.target.value }))} style={{ marginLeft: 8, flex: 1 }} /></label>
                    </>
                  )}
                  <button type="button" className="tbtn" onClick={async () => {
                    await api("/api/config", { method: "POST", body: JSON.stringify({ ...config, provider: engineProvider(engine, config) }) });
                    setStatus("已保存引擎设置。");
                  }}>保存密钥</button>
                </div>
                <div className="group">
                  <h4>ODA · 术语表 · 更新</h4>
                  <p className="note">
                    {oda.installed ? `已检测到 ${oda.path}` : "未装 ODA，DWG 请另存 DXF。"}
                    <br />术语表 {glossary} 条。
                  </p>
                  <button type="button" className="tbtn" onClick={checkUpdates}>检查更新</button>
                  {updateMsg && <p className="note">{updateMsg}</p>}
                </div>
              </div>
            </div>
          </>
        )}
      </div>

      <footer className="foot">
        <span className="live">{oda.installed ? "ODA 已安装" : "ODA 未安装 · DXF 仍可译"}</span>
        <span>术语表 <b>{glossary}</b></span>
        <span>去重前 <b>{rows.length}</b></span>
        <span>去重后 <b>{visibleRows.length}</b></span>
        <span>{status}</span>
        <span style={{ marginLeft: "auto" }}>
          <button type="button" className="tbtn" onClick={checkUpdates}>检查更新</button>
        </span>
      </footer>
    </div>
  );
}
