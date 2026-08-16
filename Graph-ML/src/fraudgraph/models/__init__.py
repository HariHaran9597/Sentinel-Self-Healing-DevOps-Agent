"""GNN models: GCN, GraphSAGE, and an MLP ablation baseline."""

from fraudgraph.models.gcn import GCN
from fraudgraph.models.graphsage import GraphSAGE
from fraudgraph.models.mlp import MLP

MODEL_REGISTRY: dict[str, type] = {
    "gcn": GCN,
    "graphsage": GraphSAGE,
    "mlp": MLP,
}

__all__ = ["GCN", "GraphSAGE", "MLP", "MODEL_REGISTRY"]
