"""MLP ablation baseline — same node features, no graph structure.

If the GNNs cannot beat this model, the graph signal isn't earning its
keep. Keeping it in the comparison table is the honest control.
"""

from __future__ import annotations

import torch
import torch.nn.functional as F
from torch import Tensor


class MLP(torch.nn.Module):
    def __init__(self, in_dim: int, hidden_dim: int = 64, embedding_dim: int = 32, dropout: float = 0.5) -> None:
        super().__init__()
        self.fc1 = torch.nn.Linear(in_dim, hidden_dim)
        self.fc2 = torch.nn.Linear(hidden_dim, embedding_dim)
        self.head = torch.nn.Linear(embedding_dim, 1)
        self.dropout = dropout

    def forward(self, x: Tensor, edge_index: Tensor | None = None) -> Tensor:
        x = F.dropout(x, p=self.dropout, training=self.training)
        x = F.relu(self.fc1(x))
        x = F.dropout(x, p=self.dropout, training=self.training)
        x = F.relu(self.fc2(x))
        return self.head(x).squeeze(-1)
