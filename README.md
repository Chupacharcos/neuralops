# NeuralOps

Autonomous agent system that runs 24/7 to maintain, promote, and grow the [adrianmoreno-dev.com](https://adrianmoreno-dev.com) portfolio.

## Architecture

Agents are organized in four clusters:

| Cluster | Description |
|---------|-------------|
| **Maintenance** | Code review, tests, dependency watch, backups, model drift detection, GitHub sync, error repair |
| **Promotion** | Lead scraping + scoring, email drafting + sending, content creation, Twitter publishing |
| **Intelligence** | Project builder, SEO monitor, project auto-onboarding, project evaluator, MetaAgent, portfolio reordering, recommendation router |
| **Polling** | Email tracker, analytics parser, social listener, competitor watcher — driven by LangGraph |

## LangGraph routing

The continuous process (`neuralops_continuous.py`) uses a **LangGraph StateGraph** with LLM-driven routing. Instead of fixed schedules for all agents, a router node (LLaMA 3.3 70B via Groq) decides each cycle which check to run — demo health, service health, API responses, or project scores — and cycles back every 60 seconds.

Four fixed-interval agents run alongside the graph:
- `email_tracker` — every 15 min
- `analytics_parser` — every 15 min
- `social_listener` — every 60 min
- `competitor_watcher` — every 60 min

## Directory structure

```
agents/
  maintenance/    — code_review, test_runner, dependency_watch, backup_verifier,
                    model_drift_detector, github_sync, error_repair, control_agent
  promotion/      — lead_scraper, lead_scorer, email_drafter, email_sender,
                    content_creator, twitter_publisher
  intelligence/   — project_builder, seo_monitor, project_auto_onboarding,
                    project_evaluator, meta_agent, portfolio_reorder,
                    recommendation_router
core/
  agent_status.py   — shared status reporting (writes agent_status.json)
  bandit.py         — epsilon-greedy multi-armed bandit for email A/B testing
  confirmation_queue.py — Telegram confirmation workflow (AUTO vs CONFIRM)
  memory.py         — ChromaDB wrapper (collections: events, pending_actions, ...)
  telegram_bot.py   — Telegram alerts + inline keyboard buttons
graph/
  neuralops_graph.py  — LangGraph StateGraph definition
  state.py            — NeuralOpsState TypedDict
scrapers/           — sector-specific lead scrapers
templates/          — email templates (HTML)
logs/               — runtime logs (gitignored, .gitkeep tracked)
memory/             — ChromaDB data (gitignored, .gitkeep tracked)
```

## Running agents

Individual agents via cron launcher:

```bash
cd /var/www/neuralops
python neuralops_cron.py <agent_name>

# Examples:
python neuralops_cron.py control_agent
python neuralops_cron.py daily_reporter
python neuralops_cron.py meta_agent
python neuralops_cron.py project_evaluator
```

Continuous process (managed by systemd):

```bash
sudo systemctl status neuralops
sudo journalctl -u neuralops -f
```

## Telegram integration

All agents report to a private Telegram bot. The Telegram interface supports:
- Status alerts (info / warning / error levels)
- **Inline keyboard confirmations** for high-impact actions (new projects, posts, portfolio changes)
- Daily summary at 21:45 UTC from `daily_reporter`
- Weekly report every Monday at 08:00 from `meta_agent`

## Tech stack

- **Python 3.11** + asyncio
- **LangChain** + **LangGraph** — agent orchestration and LLM routing
- **Groq API** — LLaMA 3.3 70B inference
- **ChromaDB** — vector memory (events, pending actions, project scores, email drafts)
- **python-telegram-bot** — Telegram notifications and button confirmations
- **Epsilon-greedy Bandit** — email template A/B testing

## Environment

Copy `.env.example` (not included) and fill in:

```
GROQ_API_KEY=
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
GITHUB_TOKEN=
OPENAI_API_KEY=       # optional, fallback
SERPAPI_KEY=          # SEO monitor
GSC_SITE_URL=         # Google Search Console
```
