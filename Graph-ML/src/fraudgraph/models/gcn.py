"""2-layer Graph Convolutional Network (Kipf & Welling, 2017)."""

from __future__ import annotations

import torch
import torch.nn.functional as F
from torch import Tensor
from torch_geometric.nn import GCNConv


class GCN(torch.nn.Module):
    """Node-level fraud classifier using spectral graph convolutions."""

    def __init__(self, in_dim: int, hidden_dim: int = 64, embedding_dim: int = 32, dropout: float = 0.5) -> None:
        super().__init__()
        self.conv1 = GCNConv(in_dim, hidden_dim)
        self.conv2 = GCNConv(hidden_dim, embedding_dim)
        self.head = torch.nn.Linear(embedding_dim, 1)
        self.dropout = dropout

    def forward(self, x: Tensor, edge_index: Tensor) -> Tensor:
        x = F.dropout(x, p=self.dropout, training=self.training)
        x = F.relu(self.conv1(x, edge_index))
        x = F.dropout(x, p=self.dropout, training=self.training)
        x = F.relu(self.conv2(x, edge_index))
        return self.head(x).squeeze(-1)
