import { useCallback, useEffect, useMemo, useState } from "react";
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
    throw new Error(data.detail || data.message || `HTTP ${response.status}`);
  }
  return data;
}

function modeKey(source, target) {
  if (source === "zh-Hans" && target === "en") return "zh_to_en";
  if (source === "en" && target === "zh-Hans") return "en_to_zh";
  if (source === "zh-Hans" && target === "fr") return "zh_to_fr";
  if (source === "fr" && target === "zh-Hans") return "fr_to_zh";
  return `${source}_to_${target}`;
}

function engineProvider(engine, config) {
  if (engine === "local") return "ollama";
  if (engine === "custom") return "openai";
  return config.provider === "azure" ? "azure" : "deepl";
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
    dims: false,
    model: true,
    paper: true,
    frozen: false,
    locked: false,
    off: false,
  });
  const [oda, setOda] = useState({ installed: false, path: "" });
  const [glossary, setGlossary] = useState(0);
  const [status, setStatus] = useState("放入 DWG / DXF，提取文字后再译。");
  const [config, setConfig] = useState({ provider: "deepl", deepl_key: "", azure_key: "", azure_region: "", output_dir: "" });
  const [batch, setBatch] = useState({ tasks: [], running: false });
  const [updateMsg, setUpdateMsg] = useState("");
  const [busy, setBusy] = useState(false);

  const visibleRows = useMemo(() => {
    return rows.filter((row) => {
      if (filters.dupes && row.duplicate) return false;
      if (filters.numbers && /^[\d.\-\s]+$/.test(row.source)) return false;
      return true;
    });
  }, [rows, filters]);

  const selected = files.find((item) => item.path === current);

  const refreshMeta = useCallback(async () => {
    try {
      const [odaStatus, assets, cfg] = await Promise.all([
        api("/api/odafc-status"),
        api("/api/language-assets"),
        api("/api/config"),
      ]);
      setOda(odaStatus);
      setGlossary((assets.builtin_terms || []).length + (assets.terms || []).length);
      setConfig(cfg);
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

  async function openDrawings() {
    const native = py();
    let paths = [];
    if (native?.pick_cad_files) {
      const picked = await native.pick_cad_files();
      paths = picked?.paths || [];
    }
    if (!paths.length) {
      setStatus("没有选择文件。");
      return;
    }
    const next = paths.map((path) => ({
      path,
      name: path.split(/[/\\]/).pop(),
      ext: path.toLowerCase().endsWith(".dxf") ? "DXF" : "DWG",
    }));
    setFiles(next);
    setCurrent(next[0].path);
    await extractFile(next[0].path);
  }

  async function extractFile(path) {
    setBusy(true);
    setStatus("正在提取文字…");
    try {
      const data = await api("/api/drawings/extract", {
        method: "POST",
        body: JSON.stringify({
          path,
          include_blocks: params.attribs,
          translation_mode: modeKey(sourceLang, targetLang),
        }),
      });
      setRows(data.items || []);
      setStatus(`提取 ${data.count} 条，去重后 ${data.unique}。`);
    } catch (error) {
      setRows([]);
      setStatus(error.message);
    } finally {
      setBusy(false);
    }
  }

  async function runTranslate() {
    if (!current) {
      setStatus("先打开图纸。");
      return;
    }
    const provider = engineProvider(engine, config);
    if (provider === "deepl" && !config.deepl_key) {
      setStatus("云引擎需要 DeepL Key，在参数里填写。");
      setSheet(true);
      return;
    }
    if (provider === "azure" && !config.azure_key) {
      setStatus("云引擎需要 Azure Key，在参数里填写。");
      setSheet(true);
      return;
    }
    setBusy(true);
    setStatus("正在翻译并写回…");
    try {
      const named = await api(
        `/api/default-output-name?mode=${encodeURIComponent(modeKey(sourceLang, targetLang))}&base=${encodeURIComponent(selected?.name?.replace(/\.[^.]+$/, "") || "drawing")}`
      );
      await api("/api/translate", {
        method: "POST",
        body: JSON.stringify({
          input_file: current,
          output_dir: config.output_dir,
          output_name: named.name,
          translation_mode: modeKey(sourceLang, targetLang),
          translate_blocks: params.attribs,
          deepl_key: config.deepl_key,
          provider,
          azure_key: config.azure_key,
          azure_region: config.azure_region,
          project_package_path: config.project_package_path || "",
        }),
      });
      setStatus("已开始翻译，完成后看输出目录。");
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
    const provider = engineProvider(engine, config);
    try {
      await api("/api/batch/add", { method: "POST", body: JSON.stringify({ files: paths }) });
      await api("/api/batch/start", {
        method: "POST",
        body: JSON.stringify({
          output_dir: config.output_dir,
          translation_mode: modeKey(sourceLang, targetLang),
          translate_blocks: params.attribs,
          output_format: "source",
          deepl_key: config.deepl_key,
          provider,
          azure_key: config.azure_key,
          azure_region: config.azure_region,
        }),
      });
      setTab("export");
      setStatus("批量导出已开始。");
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
    } catch (error) {
      setUpdateMsg(error.message);
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
      }
    } else {
      setSheet(true);
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
        ) : (
          <>
            <button type="button" className="tbtn pri" disabled={busy} onClick={runTranslate}>翻译</button>
            <button type="button" className="tbtn" disabled={busy} onClick={runTranslate}>写回</button>
          </>
        )}
      </header>

      <div className="body">
        <aside className="side">
          <h2>{tab === "export" ? `待导出 · ${files.length}` : "已打开"}</h2>
          {files.map((file) => (
            <div
              key={file.path}
              className={`item${file.path === current ? " on" : ""}`}
              onClick={() => {
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
                    <th style={{ width: 36 }}><input type="checkbox" defaultChecked aria-label="全选" /></th>
                    <th>原文</th>
                    <th>译文</th>
                    <th>图层 / 类型</th>
                  </tr>
                </thead>
                <tbody>
                  {visibleRows.map((row, index) => (
                    <tr key={row.id} className={index === 0 ? "on" : row.duplicate ? "skip" : ""}>
                      <td><input type="checkbox" defaultChecked={!row.duplicate} /></td>
                      <td className="src">{row.source}</td>
                      <td>
                        <input
                          value={row.target}
                          onChange={(event) => {
                            const value = event.target.value;
                            setRows((prev) => prev.map((item) => (item.id === row.id ? { ...item, target: value } : item)));
                          }}
                          style={{ width: "100%", border: 0, background: "transparent", color: "inherit", font: "inherit" }}
                        />
                      </td>
                      <td className="kind">{row.layer} · {row.type}</td>
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
            </aside>
          </>
        )}

        {tab === "import" && (
          <section className="main">
            <div className="filters">批量导入：把译好的表格写回图纸（v0.1 先走「写回」）。</div>
            <div className="table" style={{ padding: 24, color: "var(--muted)" }}>
              打开图纸后用常规处理或批量导出。人工 Excel 回填放在后续版本。
            </div>
          </section>
        )}

        {sheet && (
          <>
            <div className="dim" onClick={() => setSheet(false)} />
            <div className="sheet" role="dialog" aria-label="参数">
              <div className="sheet-h">
                <span>参数</span>
                <button type="button" className="done" onClick={() => setSheet(false)}>完成</button>
              </div>
              <div className="sheet-b">
                <div className="group">
                  <h4>导入范围</h4>
                  <label><input type="checkbox" checked={params.attribs} onChange={(event) => setParams((prev) => ({ ...prev, attribs: event.target.checked }))} /> 块属性</label>
                  <label><input type="checkbox" checked={params.dims} disabled onChange={(event) => setParams((prev) => ({ ...prev, dims: event.target.checked }))} /> 标注（v0.2）</label>
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
                  <p className="note">语言　中 → 英（工具栏可改）<br />云目前走 DeepL / Azure Key。</p>
                  <label>DeepL <input value={config.deepl_key || ""} onChange={(event) => setConfig((prev) => ({ ...prev, deepl_key: event.target.value }))} style={{ marginLeft: 8, flex: 1 }} /></label>
                  <label>Azure <input value={config.azure_key || ""} onChange={(event) => setConfig((prev) => ({ ...prev, azure_key: event.target.value }))} style={{ marginLeft: 8, flex: 1 }} /></label>
                  <button type="button" className="tbtn" onClick={async () => {
                    await api("/api/config", { method: "POST", body: JSON.stringify(config) });
                    setStatus("已保存密钥。");
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
