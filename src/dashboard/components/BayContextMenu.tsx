type Props = {
  open: boolean;
  x: number;
  y: number;
  onRename: () => void;
  onToggleType: () => void;
  onDelete: () => void;
};

export function BayContextMenu({ open, x, y, onRename, onToggleType, onDelete }: Props) {
  if (!open) return null;
  return (
    <div
      id="bay-context-menu"
      className="bay-ctx"
      style={{ left: x, top: y }}
      role="menu"
    >
      <button type="button" onClick={onRename}>
        <span>✏️</span> Rename Bay
      </button>
      <button type="button" onClick={onToggleType}>
        <span>🏷️</span> Toggle Vehicle Bay / Tool Station
      </button>
      <div className="bay-ctx__rule" />
      <button type="button" className="is-danger" onClick={onDelete}>
        <span>🗑️</span> Delete Bay
      </button>
    </div>
  );
}
