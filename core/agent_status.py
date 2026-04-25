"""Escribe el estado real de cada agente en un JSON compartido para la house view."""
import json
import time
import fcntl
import logging
from pathlib import Path

STATUS_FILE = Path("/var/www/neuralops/agent_status.json")
LOCK_FILE = Path("/var/www/neuralops/agent_status.lock")
logger = logging.getLogger(__name__)

# Mapeo clave_cron → nombre display
AGENT_DISPLAY = {
    "demo_watcher":        "DemoWatcher",
    "performance_watch":   "PerformanceWatch",
    "response_handler":    "ResponseHandler",
    "email_tracker":       "EmailTracker",
    "analytics_parser":    "AnalyticsParser",
    "social_listener":     "SocialListener",
    "competitor_watcher":  "CompetitorWatcher",
    "health_agent":        "HealthAgent",
    "control_agent":       "ControlAgent",
    "service_monitor":     "ServiceMonitor",
    "demo_ci":             "DemoCI",
    "code_review":         "CodeReview",
    "test_runner":         "TestRunner",
    "dependency_watch":    "DependencyWatch",
    "backup_verifier":     "BackupVerifier",
    "model_drift":         "ModelDriftDetector",
    "github_sync":         "GithubSync",
    "portfolio_updater":   "PortfolioUpdater",
    "lead_scraper":        "LeadScraper",
    "lead_scorer":         "LeadScorer",
    "email_drafter":       "EmailDrafter",
    "email_sender":        "EmailSender",
    "content_creator":     "ContentCreator",
    "twitter_publisher":   "TwitterPublisher",
    "project_builder":     "ProjectBuilder",
    "seo_monitor":         "SeoMonitor",
    "project_onboarding":  "ProjectOnboarding",
    "project_evaluator":   "ProjectEvaluator",
    "meta_agent":          "MetaAgent",
    "portfolio_reorder":   "PortfolioReorder",
    "recommendation_router": "RecommendationRouter",
    "error_repair":        "ErrorRepairAgent",
}


def report(agent_key: str, message: str, level: str = "info"):
    """
    Actualiza el estado de un agente en agent_status.json.
    agent_key: clave cron (demo_watcher) o nombre display (DemoWatcher)
    message: texto legible que se mostrará en la house
    level: 'info' | 'ok' | 'warning' | 'error'
    """
    display = AGENT_DISPLAY.get(agent_key, agent_key)
    entry = {
        "msg":   message,
        "ts":    time.strftime("%H:%M"),
        "level": level,
        "epoch": int(time.time()),
    }
    # File lock cross-process: lee + modifica + escribe atómicamente
    try:
        with open(LOCK_FILE, "w") as lock_f:
            fcntl.flock(lock_f.fileno(), fcntl.LOCK_EX)
            try:
                data: dict = {}
                if STATUS_FILE.exists():
                    try:
                        data = json.loads(STATUS_FILE.read_text())
                    except Exception:
                        pass
                data[display] = entry
                STATUS_FILE.write_text(json.dumps(data, ensure_ascii=False))
            finally:
                fcntl.flock(lock_f.fileno(), fcntl.LOCK_UN)
    except Exception as e:
        logger.warning(f"[AgentStatus] no se pudo escribir: {e}")
