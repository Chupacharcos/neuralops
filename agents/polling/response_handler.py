"""Stub — lee bandeja IMAP cada 5 min para emails de leads conocidos."""
import logging
from graph.state import NeuralOpsState

logger = logging.getLogger(__name__)


async def response_handler(state: NeuralOpsState) -> NeuralOpsState:
    # TODO: implementar IMAP con imaplib2 cuando leads.db tenga datos
    logger.debug("[ResponseHandler] stub — sin leads aún")
    return state
