export type FieldSavePhase = "idle" | "editing" | "queued" | "saving" | "saved" | "fading" | "failed";

export type FieldSaveState = {
  phase: FieldSavePhase;
  editingValue: string;
  committedValue: string;
  confirmedValue: string;
  editRevision: number;
  committedRevision: number;
  confirmedRevision: number;
  visualText: "" | "Saving…" | "✓ Saved" | "! Not saved";
};

export type FieldSaveEvent =
  | { type: "input"; value: string }
  | { type: "commit" }
  | { type: "request-started"; revision: number }
  | { type: "saving-delay-expired"; revision: number }
  | { type: "request-succeeded"; revision: number; value: string }
  | { type: "request-failed"; revision: number }
  | { type: "success-hold-expired"; revision: number; reducedMotion: boolean }
  | { type: "fade-expired"; revision: number }
  | { type: "retry" }
  | { type: "revert" };

export function createFieldSaveState(value: string): FieldSaveState {
  return {
    phase: "idle",
    editingValue: value,
    committedValue: value,
    confirmedValue: value,
    editRevision: 0,
    committedRevision: 0,
    confirmedRevision: 0,
    visualText: "",
  };
}

export function transitionFieldSave(state: FieldSaveState, event: FieldSaveEvent): FieldSaveState {
  if (event.type === "input") {
    if (event.value === state.editingValue) return state;
    const editRevision = state.editRevision + 1;
    if (event.value === state.confirmedValue && state.committedRevision <= state.confirmedRevision) {
      return { ...state, editingValue: event.value, committedValue: event.value, editRevision, phase: "idle", visualText: "" };
    }
    return { ...state, editingValue: event.value, editRevision, phase: "editing", visualText: "" };
  }

  if (event.type === "commit") {
    if (state.editingValue === state.committedValue) return state;
    if (state.editingValue === state.confirmedValue) {
      return {
        ...state,
        committedValue: state.editingValue,
        committedRevision: state.editRevision,
        confirmedRevision: state.editRevision,
        phase: "idle",
        visualText: "",
      };
    }
    return {
      ...state,
      committedValue: state.editingValue,
      committedRevision: state.editRevision,
      phase: "queued",
      visualText: "",
    };
  }

  if (event.type === "request-started") {
    if (event.revision !== state.committedRevision || event.revision <= state.confirmedRevision) return state;
    return { ...state, phase: "queued", visualText: "" };
  }

  if (event.type === "saving-delay-expired") {
    if (event.revision !== state.committedRevision || state.phase !== "queued") return state;
    return { ...state, phase: "saving", visualText: "Saving…" };
  }

  if (event.type === "request-succeeded") {
    const confirmedRevision = Math.max(state.confirmedRevision, event.revision);
    const confirmedValue = event.revision >= state.confirmedRevision ? event.value : state.confirmedValue;
    if (event.revision !== state.committedRevision || event.value !== state.committedValue || state.editingValue !== event.value) {
      const phase = state.committedRevision > confirmedRevision
        ? "queued"
        : state.editingValue !== confirmedValue ? "editing" : "idle";
      return { ...state, confirmedRevision, confirmedValue, phase, visualText: "" };
    }
    return { ...state, confirmedRevision, confirmedValue, phase: "saved", visualText: "✓ Saved" };
  }

  if (event.type === "request-failed") {
    if (event.revision !== state.committedRevision || event.revision <= state.confirmedRevision) return state;
    return { ...state, phase: "failed", visualText: "! Not saved" };
  }

  if (event.type === "success-hold-expired") {
    if (event.revision !== state.committedRevision || state.phase !== "saved") return state;
    return event.reducedMotion
      ? { ...state, phase: "idle", visualText: "" }
      : { ...state, phase: "fading", visualText: "✓ Saved" };
  }

  if (event.type === "fade-expired") {
    if (event.revision !== state.committedRevision || state.phase !== "fading") return state;
    return { ...state, phase: "idle", visualText: "" };
  }

  if (event.type === "retry") {
    if (state.committedRevision <= state.confirmedRevision) return state;
    return { ...state, phase: "queued", visualText: "" };
  }

  if (state.editingValue !== state.confirmedValue) return state;
  return {
    ...state,
    committedValue: state.confirmedValue,
    committedRevision: state.confirmedRevision,
    phase: "idle",
    visualText: "",
  };
}
