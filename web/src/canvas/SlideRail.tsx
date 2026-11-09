import { useState } from "react";
import type { SlideData, SpecView } from "../api/types";

// The memo's slides: go to one, reorder by dragging, duplicate, delete, add one of a section on one of its
// layouts. What the firm's rules do not allow is shown disabled here; the server checks every change anyway
// (the order rules too) and says why when it refuses.

type Section = SpecView["sections"][string];
type Op = { type: string; target: string; payload?: Record<string, unknown> };

function allowsAnother(sec: Section | undefined, count: number): boolean {
  if (!sec) return false;
  if (sec.count === "one") return count < 1;
  if (sec.count === "n") return sec.count_n === null || count < sec.count_n;
  return true;
}

export function SlideRail({
  memoId,
  slides,
  spec,
  focusId,
  onFocus,
  onOp,
}: {
  memoId: string;
  slides: SlideData[];
  spec: SpecView;
  focusId: string | null;
  onFocus: (slideId: string) => void;
  onOp: (op: Op) => void;
}) {
  const [dragging, setDragging] = useState<string | null>(null);
  const [dropAt, setDropAt] = useState<number | null>(null);
  const [adding, setAdding] = useState(false);
  const countOf = (sectionId: string | null) => slides.filter((s) => s.section_id === sectionId).length;
  const sectionOf = (s: SlideData) => (s.section_id ? spec.sections[s.section_id] : undefined);
  const focusIndex = Math.max(0, slides.findIndex((s) => s.id === focusId));

  const drop = (index: number) => {
    const moving = slides.find((s) => s.id === dragging);
    setDragging(null);
    setDropAt(null);
    if (!moving) return;
    const to = index > moving.position ? index - 1 : index; // the slide leaves its place before taking the new one
    if (to !== moving.position) onOp({ type: "move_slide", target: moving.id, payload: { position: to } });
  };

  return (
    <nav className="rail" onDragOver={(e) => e.preventDefault()}>
      {slides.map((s, i) => {
        const sec = sectionOf(s);
        const locked = sec?.status === "locked";
        const canDuplicate = allowsAnother(sec, countOf(s.section_id));
        const required = sec && (sec.status === "locked" || sec.status === "mandatory") && countOf(s.section_id) <= 1;
        return (
          <div
            key={s.id}
            className={`rail-slot${dropAt === i ? " drop-before" : ""}`}
            onDragOver={(e) => {
              if (!dragging) return;
              e.preventDefault();
              const box = (e.currentTarget as HTMLElement).getBoundingClientRect();
              setDropAt(e.clientY < box.top + box.height / 2 ? i : i + 1);
            }}
            onDrop={(e) => {
              e.preventDefault();
              drop(dropAt ?? i);
            }}
          >
            <a
              href={`#slide-${s.id}`}
              className={`rail-item${(focusId ?? slides[0]?.id) === s.id ? " focus" : ""}${dragging === s.id ? " dragging" : ""}`}
              draggable={!locked}
              title={`${s.position + 1} · ${sec?.name ?? s.layout ?? "Slide"}${s.layout ? ` (${s.layout})` : ""}${locked ? " · placed by the firm" : " · drag to reorder"}`}
              onDragStart={(e) => {
                setDragging(s.id);
                e.dataTransfer.effectAllowed = "move";
                e.dataTransfer.setData("text/plain", s.id);
              }}
              onDragEnd={() => {
                setDragging(null);
                setDropAt(null);
              }}
              onClick={() => onFocus(s.id)}
            >
              <span className="rail-n">{s.position + 1}</span>
              <span className="rail-name">{sec?.name ?? s.layout ?? "Slide"}</span>
              {locked && (
                <svg
                  className="rail-lock"
                  viewBox="0 0 24 24"
                  width="12"
                  height="12"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  aria-label="Placed by the firm"
                >
                  <rect x="4" y="11" width="16" height="10" rx="2" />
                  <path d="M8 11V7a4 4 0 0 1 8 0v4" />
                </svg>
              )}
            </a>
            <span className="rail-actions">
              <button
                className="icon"
                title={canDuplicate ? "Duplicate" : `The firm allows ${sec?.count === "one" ? "one" : "no more"} ${sec?.name ?? ""} slide`}
                disabled={!canDuplicate}
                onClick={() => onOp({ type: "duplicate_slide", target: s.id })}
              >
                ⧉
              </button>
              <button
                className="icon"
                title={required ? `${sec?.name} is required by the firm` : "Delete"}
                disabled={Boolean(required)}
                onClick={() => {
                  if (window.confirm(`Delete slide ${s.position + 1} (${sec?.name ?? s.layout})?`)) onOp({ type: "delete_slide", target: s.id });
                }}
              >
                ✕
              </button>
            </span>
          </div>
        );
      })}
      <div
        className={`rail-slot end${dropAt === slides.length ? " drop-before" : ""}`}
        onDragOver={(e) => {
          if (!dragging) return;
          e.preventDefault();
          setDropAt(slides.length);
        }}
        onDrop={(e) => {
          e.preventDefault();
          drop(slides.length);
        }}
      >
        <button className="rail-add" onClick={() => setAdding(true)}>
          + Add slide
        </button>
      </div>
      {adding && (
        <div className="overlay" onClick={() => setAdding(false)}>
          <div className="dialog wide-dialog" onClick={(e) => e.stopPropagation()}>
            <h2>Add a slide</h2>
            <p className="muted">
              The firm's sections and their layouts. It goes after slide {focusIndex + 1}; the firm's order rules may place it elsewhere or
              refuse it. It comes with the layout's boxes to fill.
            </p>
            <div className="add-sections">
              {Object.entries(spec.sections).map(([id, sec]) => {
                const open = allowsAnother(sec, countOf(id));
                return (
                  <div key={id} className={`add-section${open ? "" : " full"}`}>
                    <div>
                      <strong>{sec.name}</strong>{" "}
                      <span className="muted small">
                        {sec.status} · {sec.count === "one" ? "one slide" : sec.count === "n" ? `up to ${sec.count_n}` : "any number"}
                        {countOf(id) ? ` · ${countOf(id)} in the memo` : ""}
                      </span>
                    </div>
                    <div className="add-layouts">
                      {sec.layouts.length === 0 && <span className="muted small">no layout</span>}
                      {sec.layouts.map((l) => (
                        <button
                          key={l.id}
                          disabled={!open}
                          title={open ? "" : "The firm allows no more slides of this section"}
                          onClick={() => {
                            setAdding(false);
                            onOp({ type: "add_slide", target: memoId, payload: { section_id: id, layout_id: l.id, position: focusIndex + 1 } });
                          }}
                        >
                          {l.name}
                          {l.blank && <span className="muted small"> · free</span>}
                        </button>
                      ))}
                    </div>
                  </div>
                );
              })}
            </div>
            <div className="dialog-actions">
              <button onClick={() => setAdding(false)}>Cancel</button>
            </div>
          </div>
        </div>
      )}
    </nav>
  );
}
