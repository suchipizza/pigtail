// D7 `/briefs/new` and `/briefs/:id/edit`: guided form and YAML view of one brief (R18.2).
// Saving an existing brief creates a new version (R18.4); unchanged content creates none.
import { useState, type FormEvent } from "react";
import { api, ApiError, type BriefData, type BriefWrite, type FieldError } from "../api";
import { BriefForm } from "../components/BriefForm";
import { asString, getPath } from "../briefDraft";
import { Link, navigate } from "../router";
import { useAsync } from "../useAsync";

type Tab = "form" | "yaml";

interface Loaded {
  brief: BriefData;
  yaml: string;
  version: number | null;
}

export function BriefEditorPage({ id, importYaml = false }: { id?: string; importYaml?: boolean }) {
  const source = useAsync<Loaded>(
    () =>
      id
        ? api.brief(id).then((b) => ({ brief: b.brief, yaml: b.yaml, version: b.version }))
        : api.briefTemplate().then((t) => ({ brief: t.brief, yaml: t.yaml, version: null })),
    `editor:${id ?? "new"}`,
  );
  const [edits, setEdits] = useState<BriefData | null>(null);
  const [yamlEdits, setYamlEdits] = useState<string | null>(importYaml ? "" : null);
  const [tab, setTab] = useState<Tab>(importYaml ? "yaml" : "form");
  const [errors, setErrors] = useState<FieldError[]>([]);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (source.status === "error") {
    return (
      <p className="error" role="alert">
        {source.error.message}
      </p>
    );
  }
  if (!source.data) return <p className="muted">Loading…</p>;
  const loaded = source.data;
  const draft = edits ?? loaded.brief;
  const yamlText = yamlEdits ?? loaded.yaml;

  const fail = (err: unknown) => {
    if (err instanceof ApiError && err.errors.length > 0) {
      setErrors(err.errors);
      setMessage(`Not saved: ${err.errors.length} problem(s). Each is shown next to its field.`);
    } else {
      setErrors([]);
      setMessage(err instanceof Error ? err.message : "Request failed.");
    }
  };

  const body = (): BriefWrite => {
    const b: BriefWrite = tab === "yaml" ? { yaml: yamlText } : { brief: draft };
    if (loaded.version !== null) b.base_version = loaded.version;
    return b;
  };

  const check = async () => {
    setBusy(true);
    try {
      const res = await api.validateBrief(tab === "yaml" ? { yaml: yamlText } : { brief: draft });
      setErrors([]);
      setWarnings(res.warnings);
      setEdits(res.brief);
      setYamlEdits(res.yaml);
      setMessage("Valid. The form and the YAML now show the same brief.");
    } catch (err) {
      fail(err);
    } finally {
      setBusy(false);
    }
  };

  const save = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    try {
      if (id) {
        const res = await api.saveBriefVersion(id, body());
        navigate(`/briefs/${id}?saved=${res.created ? res.version : "unchanged"}`);
      } else {
        const res = await api.createBrief(body());
        navigate(`/briefs/${asString(getPath(res.brief, "brief_id"))}?saved=1`);
      }
    } catch (err) {
      fail(err);
      setBusy(false);
    }
  };

  return (
    <form onSubmit={save}>
      <p className="crumbs">
        <Link href="/briefs">Briefs</Link> / {id ? `${id} (editing v${loaded.version})` : "new"}
      </p>
      <h1>{id ? "Edit brief" : "New brief"}</h1>
      {!id && (
        <p className="muted">
          Pre-filled with the synthetic example. Change every field to describe your own project.
        </p>
      )}
      <div className="tabs" role="tablist">
        <button type="button" role="tab" aria-selected={tab === "form"} onClick={() => setTab("form")}>
          Guided form
        </button>
        <button type="button" role="tab" aria-selected={tab === "yaml"} onClick={() => setTab("yaml")}>
          YAML (import / export)
        </button>
      </div>
      <div role="tabpanel">
        {tab === "form" ? (
          <BriefForm draft={draft} errors={errors} onChange={setEdits} lockId={Boolean(id)} />
        ) : (
          <label className="brief-field">
            YAML
            <textarea
              className="yaml"
              rows={30}
              value={yamlText}
              placeholder="Paste a brief (schema brief/v1)"
              onChange={(e) => setYamlEdits(e.target.value)}
            />
            <span className="muted small">
              Use “Check” to load this YAML into the form, or to refresh it from the form.
            </span>
          </label>
        )}
      </div>
      {tab === "yaml" && errors.length > 0 && (
        <ul className="error" role="alert">
          {errors.map((er) => (
            <li key={`${er.path}:${er.message}`}>
              <code>{er.path}</code>: {er.message}
            </li>
          ))}
        </ul>
      )}
      {message && <p role="status">{message}</p>}
      {warnings.length > 0 && (
        <ul className="muted small">
          {warnings.map((w) => (
            <li key={w}>{w}</li>
          ))}
        </ul>
      )}
      <div className="filters">
        <button type="button" onClick={check} disabled={busy}>
          Check
        </button>
        <button type="submit" disabled={busy}>
          {id ? "Save as new version" : "Create brief"}
        </button>
        <Link href={id ? `/briefs/${id}` : "/briefs"}>Cancel</Link>
      </div>
    </form>
  );
}
