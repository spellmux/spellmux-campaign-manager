import { render } from "preact";
import { useEffect, useState } from "preact/hooks";

import "./styles.css";

type Campaign = {
  id: string;
  slug: string;
  name: string;
  description: string;
  game_system: string;
  play_mode: string;
  vtt: string;
  character_source: string;
  notes: string;
  role: string;
};

type CampaignEvent = CustomEvent<Campaign>;

type GuideEntry = {
  id: string;
  kind: string;
  canonical_name: string;
  aliases: string[];
  notes: string;
  visibility: "gm" | "player";
};

type GuideState = { campaignId: string; role: string; entries: GuideEntry[] };
type GuideEvent = CustomEvent<GuideState>;

const guideKinds = [
  "instruction", "character", "location", "faction", "item", "spell", "quest",
  "creature", "deity", "rule", "pronunciation", "other",
];

const emptyGuideEntry: GuideEntry = {
  id: "", kind: "character", canonical_name: "", aliases: [], notes: "", visibility: "gm",
};

async function apiRequest(path: string, options: RequestInit) {
  const token = sessionStorage.getItem("campaignToken");
  const response = await fetch(`/api/v1${path}`, {
    ...options,
    headers: {
      ...(options.body ? { "Content-Type": "application/json" } : {}),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...options.headers,
    },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `${response.status} ${response.statusText}`);
  }
  return response;
}

const emptyCampaign: Campaign = {
  id: "",
  slug: "",
  name: "",
  description: "",
  game_system: "",
  play_mode: "",
  vtt: "",
  character_source: "",
  notes: "",
  role: "player",
};

function campaignFromRoot(root: HTMLElement): Campaign {
  try {
    return root.dataset.campaign ? JSON.parse(root.dataset.campaign) : emptyCampaign;
  } catch {
    return emptyCampaign;
  }
}

function CampaignSettings({ root }: { root: HTMLElement }) {
  const [campaign, setCampaign] = useState<Campaign>(() => campaignFromRoot(root));
  const [draft, setDraft] = useState<Campaign>(campaign);
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    const receiveCampaign = (event: Event) => {
      const updated = (event as CampaignEvent).detail;
      setCampaign(updated);
      if (!editing || updated.id !== campaign.id) {
        setDraft(updated);
        setEditing(false);
      }
    };
    window.addEventListener("campaign-manager:campaign-settings", receiveCampaign);
    return () => window.removeEventListener("campaign-manager:campaign-settings", receiveCampaign);
  }, [campaign.id, editing]);

  useEffect(() => {
    root.dataset.dirty = editing ? "true" : "false";
    if (!editing) return;
    const protectDraft = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener("beforeunload", protectDraft);
    return () => window.removeEventListener("beforeunload", protectDraft);
  }, [editing, root]);

  const canEdit = ["owner", "gm"].includes(campaign.role);
  const update = (field: keyof Campaign, value: string) =>
    setDraft((current) => ({ ...current, [field]: value }));

  const beginEditing = () => {
    setDraft(campaign);
    setError("");
    setEditing(true);
  };

  const cancelEditing = () => {
    setDraft(campaign);
    setError("");
    setEditing(false);
  };

  const save = async (event: SubmitEvent) => {
    event.preventDefault();
    if (!draft.name.trim()) {
      setError("Campaign name is required.");
      return;
    }
    setSaving(true);
    setError("");
    try {
      const token = sessionStorage.getItem("campaignToken");
      const response = await fetch(`/api/v1/campaigns/${campaign.id}`, {
        method: "PUT",
        headers: {
          "Content-Type": "application/json",
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify({
          name: draft.name,
          description: draft.description,
          game_system: draft.game_system,
          play_mode: draft.play_mode,
          vtt: draft.vtt,
          character_source: draft.character_source,
          notes: draft.notes,
        }),
      });
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.detail || `${response.status} ${response.statusText}`);
      }
      const updated = (await response.json()) as Campaign;
      setCampaign(updated);
      setDraft(updated);
      setEditing(false);
      window.dispatchEvent(new CustomEvent("campaign-manager:campaign-updated", { detail: updated }));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to save campaign settings.");
    } finally {
      setSaving(false);
    }
  };

  if (!campaign.id) return <p class="muted">Choose a campaign to view its settings.</p>;

  if (!editing) {
    return (
      <div class="settings-view">
        <dl>
          <div><dt>Name</dt><dd>{campaign.name}</dd></div>
          <div><dt>Description</dt><dd>{campaign.description || "Not set"}</dd></div>
          <div><dt>Game system</dt><dd>{campaign.game_system || "Not set"}</dd></div>
          <div><dt>Play mode</dt><dd>{campaign.play_mode.replace("_", " ") || "Not set"}</dd></div>
          <div><dt>Virtual tabletop</dt><dd>{campaign.vtt || "Not set"}</dd></div>
          <div><dt>Character source</dt><dd>{campaign.character_source || "Not set"}</dd></div>
          <div><dt>Campaign notes</dt><dd>{campaign.notes || "Not set"}</dd></div>
        </dl>
        {canEdit && <button type="button" onClick={beginEditing}>Edit campaign settings</button>}
      </div>
    );
  }

  return (
    <form class="settings-form" onSubmit={save}>
      <label>Campaign name<input value={draft.name} maxLength={160} required onInput={(event) => update("name", event.currentTarget.value)} /></label>
      <label>Description<textarea value={draft.description} maxLength={20000} rows={5} onInput={(event) => update("description", event.currentTarget.value)} /></label>
      <label>Game system<input value={draft.game_system} maxLength={120} placeholder="D&D 5.5, Pathfinder 2e, etc." onInput={(event) => update("game_system", event.currentTarget.value)} /></label>
      <label>Play mode<select value={draft.play_mode} onChange={(event) => update("play_mode", event.currentTarget.value)}><option value="">Not set</option><option value="in_person">In person</option><option value="online">Online</option><option value="hybrid">Hybrid</option></select></label>
      <label>Virtual tabletop<input value={draft.vtt} maxLength={160} placeholder="Foundry VTT, Roll20, none, etc." onInput={(event) => update("vtt", event.currentTarget.value)} /></label>
      <label>Character source<input value={draft.character_source} maxLength={160} placeholder="Foundry, D&D Beyond, paper sheets, etc." onInput={(event) => update("character_source", event.currentTarget.value)} /></label>
      <label>Campaign notes<textarea value={draft.notes} maxLength={20000} rows={6} placeholder="Table conventions, tooling notes, or other campaign context" onInput={(event) => update("notes", event.currentTarget.value)} /></label>
      {error && <p class="editor-error" role="alert">{error}</p>}
      <div class="editor-actions"><button type="submit" disabled={saving}>{saving ? "Saving…" : "Save changes"}</button><button type="button" class="secondary" disabled={saving} onClick={cancelEditing}>Cancel</button></div>
    </form>
  );
}

function guideFromRoot(root: HTMLElement): GuideState {
  try {
    return root.dataset.guide
      ? JSON.parse(root.dataset.guide)
      : { campaignId: "", role: "player", entries: [] };
  } catch {
    return { campaignId: "", role: "player", entries: [] };
  }
}

function CampaignGuideEditor({ root }: { root: HTMLElement }) {
  const [guide, setGuide] = useState<GuideState>(() => guideFromRoot(root));
  const [draft, setDraft] = useState<GuideEntry | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [deleting, setDeleting] = useState<GuideEntry | null>(null);

  useEffect(() => {
    const receiveGuide = (event: Event) => setGuide((event as GuideEvent).detail);
    window.addEventListener("campaign-manager:campaign-guide", receiveGuide);
    return () => window.removeEventListener("campaign-manager:campaign-guide", receiveGuide);
  }, []);

  useEffect(() => {
    root.dataset.dirty = draft ? "true" : "false";
    if (!draft) return;
    const protectDraft = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener("beforeunload", protectDraft);
    return () => window.removeEventListener("beforeunload", protectDraft);
  }, [draft, root]);

  const canEdit = ["owner", "gm"].includes(guide.role);
  const update = <K extends keyof GuideEntry>(field: K, value: GuideEntry[K]) =>
    setDraft((current) => current ? { ...current, [field]: value } : current);

  const save = async (event: SubmitEvent) => {
    event.preventDefault();
    if (!draft || !draft.canonical_name.trim()) {
      setError("Canonical name is required.");
      return;
    }
    setSaving(true);
    setError("");
    try {
      const payload = {
        kind: draft.kind,
        canonical_name: draft.canonical_name.trim(),
        aliases: draft.aliases.map((alias) => alias.trim()).filter(Boolean),
        notes: draft.notes,
        visibility: draft.visibility,
      };
      await apiRequest(
        `/campaigns/${guide.campaignId}/guide${draft.id ? `/${draft.id}` : ""}`,
        { method: draft.id ? "PUT" : "POST", body: JSON.stringify(payload) },
      );
      setDraft(null);
      window.dispatchEvent(new CustomEvent("campaign-manager:guide-updated"));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to save guide entry.");
    } finally {
      setSaving(false);
    }
  };

  const remove = async () => {
    if (!deleting) return;
    setSaving(true);
    setError("");
    try {
      await apiRequest(`/campaigns/${guide.campaignId}/guide/${deleting.id}`, { method: "DELETE" });
      setDeleting(null);
      if (draft?.id === deleting.id) setDraft(null);
      window.dispatchEvent(new CustomEvent("campaign-manager:guide-updated"));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to delete guide entry.");
    } finally {
      setSaving(false);
    }
  };

  if (draft) {
    return (
      <form class="guide-entry-form" onSubmit={save}>
        <div class="editor-heading"><h3>{draft.id ? "Edit guide entry" : "New guide entry"}</h3><span class="muted">Used as campaign truth during transcription and analysis</span></div>
        <div class="guide-fields-two">
          <label>Type<select value={draft.kind} onChange={(event) => update("kind", event.currentTarget.value)}>{guideKinds.map((kind) => <option value={kind}>{kind.replace("_", " ")}</option>)}</select></label>
          <label>Visibility<select value={draft.visibility} onChange={(event) => update("visibility", event.currentTarget.value as "gm" | "player")}><option value="gm">GM only</option><option value="player">Players</option></select></label>
        </div>
        <label>Canonical name<input value={draft.canonical_name} maxLength={200} required autoFocus onInput={(event) => update("canonical_name", event.currentTarget.value)} /></label>
        <label>Aliases<input value={draft.aliases.join(", ")} placeholder="Comma-separated alternative names or spellings" onInput={(event) => update("aliases", event.currentTarget.value.split(","))} /></label>
        <label>Notes and coaching<textarea value={draft.notes} maxLength={20000} rows={12} placeholder="Description, pronunciation, established lore, or instructions for analysis" onInput={(event) => update("notes", event.currentTarget.value)} /></label>
        {error && <p class="editor-error" role="alert">{error}</p>}
        <div class="editor-actions"><button type="submit" disabled={saving}>{saving ? "Saving…" : "Save entry"}</button><button type="button" class="secondary" disabled={saving} onClick={() => { setDraft(null); setError(""); }}>Cancel</button></div>
      </form>
    );
  }

  return (
    <div class="guide-editor-view">
      <div class="guide-toolbar"><span class="muted">{guide.entries.length} {guide.entries.length === 1 ? "entry" : "entries"}</span>{canEdit && <button type="button" onClick={() => { setDraft({ ...emptyGuideEntry }); setError(""); }}>New guide entry</button>}</div>
      {error && <p class="editor-error" role="alert">{error}</p>}
      <div class="guide-entry-list">
        {guide.entries.length === 0 && <div class="guide-empty"><strong>No campaign guide entries yet</strong><span class="muted">Add canonical names and campaign facts to improve local processing.</span></div>}
        {guide.entries.map((entry) => (
          <article class="guide-entry-card" key={entry.id}>
            <div class="guide-entry-heading"><div><span class="guide-kind">{entry.kind.replace("_", " ")}</span><h3>{entry.canonical_name}</h3></div><span class="guide-visibility">{entry.visibility === "gm" ? "GM only" : "Players"}</span></div>
            {entry.aliases.length > 0 && <p class="muted">Aliases: {entry.aliases.join(", ")}</p>}
            <p class="guide-notes">{entry.notes || "No notes yet."}</p>
            {canEdit && <div class="editor-actions"><button type="button" class="secondary" onClick={() => { setDraft({ ...entry, aliases: [...entry.aliases] }); setError(""); }}>Edit</button><button type="button" class="danger secondary" onClick={() => setDeleting(entry)}>Delete</button></div>}
          </article>
        ))}
      </div>
      {deleting && <div class="confirm-panel" role="alertdialog" aria-labelledby="delete-guide-title"><strong id="delete-guide-title">Delete {deleting.canonical_name}?</strong><p>This permanently removes the entry from campaign guidance. Existing session content is unchanged.</p><div class="editor-actions"><button type="button" class="danger" disabled={saving} onClick={remove}>{saving ? "Deleting…" : "Delete entry"}</button><button type="button" class="secondary" disabled={saving} onClick={() => setDeleting(null)}>Cancel</button></div></div>}
    </div>
  );
}

const root = document.getElementById("campaign-settings-editor");
if (root) render(<CampaignSettings root={root} />, root);
const guideRoot = document.getElementById("campaign-guide-editor");
if (guideRoot) render(<CampaignGuideEditor root={guideRoot} />, guideRoot);
