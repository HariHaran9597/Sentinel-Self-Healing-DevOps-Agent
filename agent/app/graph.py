"""Phase 2 -- the brain: the LangGraph healing state machine.

    START -> classify -> retrieve -+-> diagnose -> propose -> human_gate -+-> execute_fix -> verify -> END
                                   |                                     |
                                   +-> escalate -> END                   +-> END (rejected)

`human_gate` blocks on langgraph's interrupt() (checkpointed in memory);
the FastAPI approve/reject endpoints resume the thread with Command(resume=...).
"""
from __future__ import annotations

from langgraph.graph import END, START, StateGraph

try:
    from langgraph.checkpoint.memory import InMemorySaver
except ImportError:  # older langgraph
    from langgraph.checkpoint.memory import MemorySaver as InMemorySaver

from . import nodes
from .state import AgentState


def build_graph(checkpointer=None):
    builder = StateGraph(AgentState)

    builder.add_node("classify", nodes.classify)
    builder.add_node("retrieve", nodes.retrieve)
    builder.add_node("diagnose", nodes.diagnose)
    builder.add_node("propose", nodes.propose)
    builder.add_node("human_gate", nodes.human_gate)
    builder.add_node("execute_fix", nodes.execute_fix)
    builder.add_node("verify", nodes.verify)
    builder.add_node("escalate", nodes.escalate)

    builder.add_edge(START, "classify")
    builder.add_edge("classify", "retrieve")
    builder.add_conditional_edges(
        "retrieve", nodes.route_after_retrieve, {"diagnose": "diagnose", "escalate": "escalate"}
    )
    builder.add_edge("diagnose", "propose")
    builder.add_edge("propose", "human_gate")
    builder.add_conditional_edges(
        "human_gate", nodes.route_after_gate, {"execute_fix": "execute_fix", "end": END}
    )
    builder.add_conditional_edges(
        "execute_fix", nodes.route_after_execute, {"verify": "verify", "end": END}
    )
    builder.add_edge("verify", END)
    builder.add_edge("escalate", END)

    # The checkpointer persists state across the interrupt() pause.
    return builder.compile(checkpointer=checkpointer or InMemorySaver())
