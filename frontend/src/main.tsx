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

const root = document.getElementById("campaign-settings-editor");
if (root) render(<CampaignSettings root={root} />, root);
