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

type Evidence = { quote: string; start_seconds: number | null; end_seconds: number | null };
type Proposal = {
  id: string; kind: string; title: string; body: string; aliases: string[];
  lane: "story" | "meta";
  evidence: Evidence[]; confidence: number | null; visibility: "gm" | "player";
  status: "proposed" | "approved" | "rejected"; provider: string; model: string;
};
type AnalysisJob = { status: string; error: string | null; payload: Record<string, unknown> };
type ReviewState = {
  campaignId: string; sessionId: string; role: string; filter: string;
  proposals: Proposal[]; latestJob: AnalysisJob | null;
};
type ReviewEvent = CustomEvent<ReviewState>;

type Speaker = { id: string; display_name: string; notes: string };
type SessionSummary = { id: string; title: string; session_date: string | null };
type SpeakerAssignment = {
  id: string; speaker_profile_id: string; speaker_name: string; guide_entry_id: string;
  character_name: string; session_id: string | null; session_title: string | null;
  is_primary: boolean; notes: string;
};
type SpeakersState = {
  campaignId: string; role: string; speakers: Speaker[]; characters: GuideEntry[];
  sessions: SessionSummary[]; assignments: SpeakerAssignment[];
};
type SpeakersEvent = CustomEvent<SpeakersState>;

const guideKinds = [
  "instruction", "player_character", "npc", "monster", "character", "creature", "location",
  "faction", "item", "spell", "quest", "deity", "rule", "pronunciation", "other",
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

function speakersFromRoot(root: HTMLElement): SpeakersState {
  try {
    return root.dataset.speakers
      ? JSON.parse(root.dataset.speakers)
      : { campaignId: "", role: "player", speakers: [], characters: [], sessions: [], assignments: [] };
  } catch {
    return { campaignId: "", role: "player", speakers: [], characters: [], sessions: [], assignments: [] };
  }
}

function CampaignSpeakersEditor({ root }: { root: HTMLElement }) {
  const [data, setData] = useState<SpeakersState>(() => speakersFromRoot(root));
  const [speakerDraft, setSpeakerDraft] = useState<Speaker | null>(null);
  const [assignmentDraft, setAssignmentDraft] = useState<Partial<SpeakerAssignment> | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<{ kind: "speaker" | "assignment"; id: string; name: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    const receive = (event: Event) => setData((event as SpeakersEvent).detail);
    window.addEventListener("campaign-manager:campaign-speakers", receive);
    return () => window.removeEventListener("campaign-manager:campaign-speakers", receive);
  }, []);

  const refresh = () => window.dispatchEvent(new CustomEvent("campaign-manager:speakers-updated"));
  const saveSpeaker = async (event: SubmitEvent) => {
    event.preventDefault();
    if (!speakerDraft?.display_name.trim()) return setError("Speaker name is required.");
    setBusy(true); setError("");
    try {
      await apiRequest(
        `/campaigns/${data.campaignId}/speakers${speakerDraft.id ? `/${speakerDraft.id}` : ""}`,
        { method: speakerDraft.id ? "PUT" : "POST", body: JSON.stringify({ display_name: speakerDraft.display_name, notes: speakerDraft.notes }) },
      );
      setSpeakerDraft(null); refresh();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Unable to save speaker."); }
    finally { setBusy(false); }
  };

  const saveAssignment = async (event: SubmitEvent) => {
    event.preventDefault();
    if (!assignmentDraft?.speaker_profile_id || !assignmentDraft.guide_entry_id) return;
    setBusy(true); setError("");
    try {
      const id = assignmentDraft.id;
      await apiRequest(
        `/campaigns/${data.campaignId}/speaker-character-assignments${id ? `/${id}` : ""}`,
        { method: id ? "PUT" : "POST", body: JSON.stringify({
          ...(!id ? { speaker_profile_id: assignmentDraft.speaker_profile_id } : {}),
          guide_entry_id: assignmentDraft.guide_entry_id,
          session_id: assignmentDraft.session_id || null,
          is_primary: Boolean(assignmentDraft.is_primary), notes: assignmentDraft.notes || "",
        }) },
      );
      setAssignmentDraft(null); refresh();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Unable to save character assignment."); }
    finally { setBusy(false); }
  };

  const remove = async () => {
    if (!deleteTarget) return;
    setBusy(true); setError("");
    const path = deleteTarget.kind === "speaker"
      ? `/campaigns/${data.campaignId}/speakers/${deleteTarget.id}`
      : `/campaigns/${data.campaignId}/speaker-character-assignments/${deleteTarget.id}`;
    try { await apiRequest(path, { method: "DELETE" }); setDeleteTarget(null); refresh(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Unable to delete item."); }
    finally { setBusy(false); }
  };

  if (speakerDraft) return <form class="speaker-editor-form" onSubmit={saveSpeaker}><h3>{speakerDraft.id ? "Edit speaker" : "New speaker"}</h3><label>Person’s name<input value={speakerDraft.display_name} required autoFocus onInput={(event) => setSpeakerDraft({ ...speakerDraft, display_name: event.currentTarget.value })} /></label><label>Notes<textarea rows={6} value={speakerDraft.notes} placeholder="Player, GM, guest, pronunciation, or voice notes" onInput={(event) => setSpeakerDraft({ ...speakerDraft, notes: event.currentTarget.value })} /></label>{error && <p class="editor-error">{error}</p>}<div class="editor-actions"><button disabled={busy}>{busy ? "Saving…" : "Save speaker"}</button><button type="button" class="secondary" onClick={() => setSpeakerDraft(null)}>Cancel</button></div></form>;

  if (assignmentDraft) {
    const speaker = data.speakers.find((item) => item.id === assignmentDraft.speaker_profile_id);
    return <form class="speaker-editor-form" onSubmit={saveAssignment}><div class="editor-heading"><h3>Connect {speaker?.display_name} to a Player Character</h3><span class="muted">Analysis treats this as guidance, not proof.</span></div>{data.characters.length ? <><label>Player Character<select required value={assignmentDraft.guide_entry_id || ""} onChange={(event) => setAssignmentDraft({ ...assignmentDraft, guide_entry_id: event.currentTarget.value })}><option value="">Choose a character</option>{data.characters.map((character) => <option value={character.id}>{character.canonical_name}{character.kind === "character" ? " (legacy type)" : ""}</option>)}</select></label><label>Scope<select value={assignmentDraft.session_id || ""} onChange={(event) => setAssignmentDraft({ ...assignmentDraft, session_id: event.currentTarget.value || null })}><option value="">Campaign default</option>{data.sessions.map((session) => <option value={session.id}>{session.session_date || "Undated"} — {session.title}</option>)}</select></label><label class="check-label"><input type="checkbox" checked={Boolean(assignmentDraft.is_primary)} onChange={(event) => setAssignmentDraft({ ...assignmentDraft, is_primary: event.currentTarget.checked })} /> Primary character for this scope</label><label>Assignment notes<textarea rows={4} value={assignmentDraft.notes || ""} placeholder="Optional context, such as a temporary character or guest appearance" onInput={(event) => setAssignmentDraft({ ...assignmentDraft, notes: event.currentTarget.value })} /></label></>:<div class="review-empty"><strong>No Player Characters are available</strong><span class="muted">Create or reclassify an entry as Player Character in the Campaign Guide first.</span></div>}{error && <p class="editor-error">{error}</p>}<div class="editor-actions">{data.characters.length > 0 && <button disabled={busy}>{busy ? "Saving…" : "Save assignment"}</button>}<button type="button" class="secondary" onClick={() => setAssignmentDraft(null)}>Cancel</button></div></form>;
  }

  return <div class="speaker-manager"><div class="guide-toolbar"><span class="muted">{data.speakers.length} identified {data.speakers.length === 1 ? "person" : "people"}</span><button type="button" onClick={() => setSpeakerDraft({ id: "", display_name: "", notes: "" })}>New speaker</button></div>{error && <p class="editor-error">{error}</p>}<div class="speaker-card-list">{data.speakers.map((speaker) => { const assignments = data.assignments.filter((item) => item.speaker_profile_id === speaker.id); return <article class="speaker-card" key={speaker.id}><div class="review-card-heading"><div><span class="guide-kind">Person</span><h3>{speaker.display_name}</h3></div><div class="editor-actions"><button type="button" class="secondary" onClick={() => setSpeakerDraft({ ...speaker })}>Edit</button><button type="button" class="danger secondary" onClick={() => setDeleteTarget({ kind: "speaker", id: speaker.id, name: speaker.display_name })}>Delete</button></div></div><p class="muted">{speaker.notes || "No speaker notes."}</p><div class="assignment-list">{assignments.map((assignment) => <div class="assignment-row" key={assignment.id}><div><strong>{assignment.character_name}</strong><span class="muted">{assignment.session_title || "Campaign default"}{assignment.is_primary ? " · primary" : ""}{assignment.notes ? ` · ${assignment.notes}` : ""}</span></div><div class="editor-actions"><button type="button" class="secondary" onClick={() => setAssignmentDraft({ ...assignment })}>Edit</button><button type="button" class="secondary" onClick={() => setDeleteTarget({ kind: "assignment", id: assignment.id, name: `${speaker.display_name} → ${assignment.character_name}` })}>Remove</button></div></div>)}{!assignments.length && <span class="muted">No Player Character connected yet.</span>}</div><button type="button" class="secondary" onClick={() => setAssignmentDraft({ speaker_profile_id: speaker.id, guide_entry_id: "", session_id: null, is_primary: assignments.length === 0, notes: "" })}>Connect Player Character</button></article>; })}</div>{deleteTarget && <div class="confirm-panel" role="alertdialog"><strong>{deleteTarget.kind === "speaker" ? `Delete ${deleteTarget.name}?` : `Remove ${deleteTarget.name}?`}</strong><p>{deleteTarget.kind === "speaker" ? "This also removes their character assignments and speaker-review links." : "This stops using the character relationship as analysis context."}</p><div class="editor-actions"><button class="danger" type="button" disabled={busy} onClick={remove}>{busy ? "Deleting…" : "Confirm"}</button><button type="button" class="secondary" onClick={() => setDeleteTarget(null)}>Cancel</button></div></div>}</div>;
}

function reviewFromRoot(root: HTMLElement): ReviewState {
  try {
    return root.dataset.review
      ? JSON.parse(root.dataset.review)
      : { campaignId: "", sessionId: "", role: "player", filter: "proposed", proposals: [], latestJob: null };
  } catch {
    return { campaignId: "", sessionId: "", role: "player", filter: "proposed", proposals: [], latestJob: null };
  }
}

function MorningReviewEditor({ root }: { root: HTMLElement }) {
  const [review, setReview] = useState<ReviewState>(() => reviewFromRoot(root));
  const [draft, setDraft] = useState<Proposal | null>(null);
  const [busy, setBusy] = useState<string[]>([]);
  const [error, setError] = useState("");

  useEffect(() => {
    const receiveReview = (event: Event) => setReview((event as ReviewEvent).detail);
    window.addEventListener("campaign-manager:analysis-review", receiveReview);
    return () => window.removeEventListener("campaign-manager:analysis-review", receiveReview);
  }, []);

  useEffect(() => {
    root.dataset.dirty = draft ? "true" : "false";
    if (!draft) return;
    const protectDraft = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener("beforeunload", protectDraft);
    return () => window.removeEventListener("beforeunload", protectDraft);
  }, [draft, root]);

  const canEdit = ["owner", "gm"].includes(review.role);
  const visible = review.proposals.filter((proposal) => review.filter === "all" || proposal.status === review.filter);
  const updateDraft = <K extends keyof Proposal>(field: K, value: Proposal[K]) =>
    setDraft((current) => current ? { ...current, [field]: value } : current);

  const save = async (event: SubmitEvent) => {
    event.preventDefault();
    if (!draft || !draft.title.trim()) return setError("Title is required.");
    setBusy([draft.id]);
    setError("");
    try {
      await apiRequest(
        `/campaigns/${review.campaignId}/sessions/${review.sessionId}/analysis-proposals/${draft.id}`,
        { method: "PUT", body: JSON.stringify({ title: draft.title, body: draft.body, lane: draft.lane, aliases: draft.aliases.map((alias) => alias.trim()).filter(Boolean), visibility: draft.visibility }) },
      );
      setDraft(null);
      window.dispatchEvent(new CustomEvent("campaign-manager:analysis-updated"));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to save finding.");
    } finally {
      setBusy([]);
    }
  };

  const decide = async (proposals: Proposal[], action: "approve" | "reject") => {
    if (!proposals.length) return;
    setBusy(proposals.map((proposal) => proposal.id));
    setError("");
    try {
      for (const proposal of proposals) {
        await apiRequest(
          `/campaigns/${review.campaignId}/sessions/${review.sessionId}/analysis-proposals/${proposal.id}/${action}`,
          { method: "POST" },
        );
      }
      window.dispatchEvent(new CustomEvent("campaign-manager:analysis-updated"));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : `Unable to ${action} findings.`);
      window.dispatchEvent(new CustomEvent("campaign-manager:analysis-updated"));
    } finally {
      setBusy([]);
    }
  };

  if (draft) {
    return (
      <form class="review-edit-form" onSubmit={save}>
        <div class="editor-heading"><div><span class="guide-kind">{draft.kind.replaceAll("_", " ")}</span><h3>Edit finding</h3></div><span class="review-confidence">{draft.confidence == null ? "Unscored" : `${Math.round(draft.confidence * 100)}% confidence`}</span></div>
        <label>Title<input value={draft.title} maxLength={200} required autoFocus onInput={(event) => updateDraft("title", event.currentTarget.value)} /></label>
        <label>Summary or description<textarea value={draft.body} maxLength={50000} rows={14} onInput={(event) => updateDraft("body", event.currentTarget.value)} /></label>
        <label>Aliases<input value={draft.aliases.join(", ")} onInput={(event) => updateDraft("aliases", event.currentTarget.value.split(","))} /></label>
        <label>Section<select value={draft.lane} onChange={(event) => updateDraft("lane", event.currentTarget.value as "story" | "meta")}><option value="story">Story</option><option value="meta">Table &amp; follow-up</option></select></label>
        <label>Visibility<select value={draft.visibility} onChange={(event) => updateDraft("visibility", event.currentTarget.value as "gm" | "player")}><option value="gm">GM only</option><option value="player">Players</option></select></label>
        {draft.evidence.length > 0 && <EvidenceList evidence={draft.evidence} />}
        {error && <p class="editor-error" role="alert">{error}</p>}
        <div class="editor-actions"><button type="submit" disabled={busy.length > 0}>{busy.length ? "Saving…" : "Save finding"}</button><button type="button" class="secondary" disabled={busy.length > 0} onClick={() => { setDraft(null); setError(""); }}>Cancel</button></div>
      </form>
    );
  }

  const pending = review.proposals.filter((proposal) => proposal.status === "proposed");
  const emptyRun = review.latestJob?.status === "succeeded" && review.proposals.length === 0;
  const cards = (proposals: Proposal[]) => proposals.map((proposal) => (
    <article class={`review-card review-${proposal.status}`} key={proposal.id}>
      <div class="review-card-heading"><div><span class="guide-kind">{proposal.kind.replaceAll("_", " ")}</span><h3>{proposal.title}</h3></div><div class="review-badges"><span>{proposal.visibility === "gm" ? "GM only" : "Players"}</span><span>{proposal.status}</span>{proposal.confidence != null && <span>{Math.round(proposal.confidence * 100)}%</span>}</div></div>
      <p class="review-body">{proposal.body || "No description supplied."}</p>
      {proposal.aliases.length > 0 && <p class="muted">Aliases: {proposal.aliases.join(", ")}</p>}
      <EvidenceList evidence={proposal.evidence} />
      {canEdit && proposal.status === "proposed" && <div class="editor-actions"><button type="button" class="secondary" disabled={busy.includes(proposal.id)} onClick={() => setDraft({ ...proposal, aliases: [...proposal.aliases], evidence: [...proposal.evidence] })}>Edit</button><button type="button" disabled={busy.includes(proposal.id)} onClick={() => decide([proposal], "approve")}>Approve</button><button type="button" class="secondary" disabled={busy.includes(proposal.id)} onClick={() => decide([proposal], "reject")}>Reject</button></div>}
    </article>
  ));
  return (
    <div class="morning-review-editor">
      {canEdit && pending.length > 0 && <div class="review-toolbar"><strong>{pending.length} finding{pending.length === 1 ? "" : "s"} need review</strong><div class="editor-actions"><button type="button" onClick={() => decide(pending, "approve")} disabled={busy.length > 0}>Approve all</button><button type="button" class="secondary" onClick={() => decide(pending, "reject")} disabled={busy.length > 0}>Reject all</button></div></div>}
      {error && <p class="editor-error" role="alert">{error}</p>}
      {!visible.length && <div class="review-empty"><strong>{emptyRun ? "Analysis produced no findings" : review.filter === "proposed" ? "Nothing needs review" : "No matching findings"}</strong><span class="muted">{emptyRun ? "Run analysis again with another source or model." : review.filter === "proposed" ? "Generated findings will appear here when analysis completes." : "Choose another filter."}</span></div>}
      {visible.some((proposal) => proposal.lane === "story") && <section><div class="editor-heading"><div><span class="guide-kind">Story</span><h3>Session story</h3></div><span class="muted">Recap, scenes, moments, entities, and story threads</span></div><div class="review-card-list">{cards(visible.filter((proposal) => proposal.lane === "story"))}</div></section>}
      {visible.some((proposal) => proposal.lane === "meta") && <section><div class="editor-heading"><div><span class="guide-kind">Meta</span><h3>Table &amp; follow-up</h3></div><span class="muted">Rules, research, to-dos, scheduling, and technical notes</span></div><div class="review-card-list">{cards(visible.filter((proposal) => proposal.lane === "meta"))}</div></section>}
    </div>
  );
}

function EvidenceList({ evidence }: { evidence: Evidence[] }) {
  if (!evidence.length) return null;
  return <details class="evidence-list"><summary>{evidence.length} source clip{evidence.length === 1 ? "" : "s"}</summary>{evidence.map((item, index) => <div class="evidence-item" key={`${item.start_seconds}-${index}`}><q>{item.quote}</q>{item.start_seconds != null && <button type="button" class="secondary" onClick={() => window.dispatchEvent(new CustomEvent("campaign-manager:play-evidence", { detail: { start: item.start_seconds!, end: item.end_seconds ?? item.start_seconds! + 15 } }))}>Listen at {formatSeconds(item.start_seconds)}</button>}</div>)}</details>;
}

function formatSeconds(value: number) {
  const seconds = Math.max(0, Math.floor(value));
  return [Math.floor(seconds / 3600), Math.floor((seconds % 3600) / 60), seconds % 60]
    .map((part) => String(part).padStart(2, "0")).join(":");
}

const root = document.getElementById("campaign-settings-editor");
if (root) render(<CampaignSettings root={root} />, root);
const guideRoot = document.getElementById("campaign-guide-editor");
if (guideRoot) render(<CampaignGuideEditor root={guideRoot} />, guideRoot);
const speakersRoot = document.getElementById("campaign-speakers-editor");
if (speakersRoot) render(<CampaignSpeakersEditor root={speakersRoot} />, speakersRoot);
const reviewRoot = document.getElementById("proposal-list");
if (reviewRoot) render(<MorningReviewEditor root={reviewRoot} />, reviewRoot);
