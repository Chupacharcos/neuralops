"""Stub — tracking de clicks en emails enviados a leads."""
import logging
from graph.state import NeuralOpsState

logger = logging.getLogger(__name__)


async def email_tracker(state: NeuralOpsState) -> NeuralOpsState:
    # TODO: implementar cuando leads.db tenga datos y emails enviados
    logger.debug("[EmailTracker] stub — sin emails enviados aún")
    return state
