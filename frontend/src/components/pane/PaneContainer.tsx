import { Group, Panel, Separator, type Layout } from "react-resizable-panels";
import { PaneContext } from "../../contexts/PaneContext";
import { usePaneStore, type PaneTree } from "../../store/paneStore";
import PaneHeader from "./PaneHeader";
import PageRenderer from "./PageRenderer";

interface Props {
  node: PaneTree;
  isOnly?: boolean;
}

export default function PaneContainer({ node, isOnly = false }: Props) {
  const { setActivePaneId, updateSizes } = usePaneStore();

  if (node.type === "leaf") {
    return (
      <PaneContext.Provider value={{ paneId: node.id, view: node.view }}>
        <div
          // A pane has no accessible name of its own — its content supplies one
          // — so this is how an e2e spec addresses "the other pane" to make it
          // active. The click handler below is what the active-pane key guards
          // (VideoStrip, ImageDetailPage) are read against.
          data-testid="pane-leaf"
          style={{ display: "flex", flexDirection: "column", height: "100%", overflow: "hidden" }}
          onClick={() => setActivePaneId(node.id)}
        >
          <PaneHeader paneId={node.id} view={node.view} isOnly={!!isOnly} />
          <div style={{ flex: 1, overflow: "hidden", position: "relative", display: "flex", flexDirection: "column" }}>
            <PageRenderer view={node.view} />
          </div>
        </div>
      </PaneContext.Provider>
    );
  }

  // PaneSplit
  const isHorizontal = node.direction === "horizontal";
  const panelAId = `${node.id}-a`;
  const panelBId = `${node.id}-b`;

  return (
    <Group
      orientation={isHorizontal ? "horizontal" : "vertical"}
      style={{ height: "100%" }}
      onLayoutChanged={(layout: Layout) => {
        const a = layout[panelAId];
        const b = layout[panelBId];
        if (a !== undefined && b !== undefined) {
          updateSizes(node.id, [a, b]);
        }
      }}
    >
      <Panel id={panelAId} defaultSize={node.sizes[0]} minSize={15}>
        <PaneContainer node={node.children[0]} />
      </Panel>
      <Separator
        style={{
          background: "var(--line)",
          flexShrink: 0,
          width: isHorizontal ? 4 : undefined,
          height: isHorizontal ? undefined : 4,
          cursor: isHorizontal ? "col-resize" : "row-resize",
        }}
      />
      <Panel id={panelBId} defaultSize={node.sizes[1]} minSize={15}>
        <PaneContainer node={node.children[1]} />
      </Panel>
    </Group>
  );
}
