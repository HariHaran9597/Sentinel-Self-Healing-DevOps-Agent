"""2-layer GraphSAGE (Hamilton et al., 2017) — inductive neighbour aggregation."""

from __future__ import annotations

import torch
import torch.nn.functional as F
from torch import Tensor
from torch_geometric.nn import SAGEConv


class GraphSAGE(torch.nn.Module):
    """Node-level fraud classifier via sampled-neighbour mean aggregation.

    GraphSAGE is the production-friendly variant: it learns aggregation
    functions over neighbourhoods, so embeddings for *previously unseen*
    accounts can be computed without retraining.
    """

    def __init__(self, in_dim: int, hidden_dim: int = 64, embedding_dim: int = 32, dropout: float = 0.5) -> None:
        super().__init__()
        self.conv1 = SAGEConv(in_dim, hidden_dim, aggr="mean")
        self.conv2 = SAGEConv(hidden_dim, embedding_dim, aggr="mean")
        self.head = torch.nn.Linear(embedding_dim, 1)
        self.dropout = dropout

    def forward(self, x: Tensor, edge_index: Tensor) -> Tensor:
        x = F.dropout(x, p=self.dropout, training=self.training)
        x = F.relu(self.conv1(x, edge_index))
        x = F.dropout(x, p=self.dropout, training=self.training)
        x = F.relu(self.conv2(x, edge_index))
        return self.head(x).squeeze(-1)
