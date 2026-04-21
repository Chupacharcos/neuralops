"""Twitter/X API v2 client wrapper via tweepy."""
import os
import logging
import tweepy
from dotenv import load_dotenv

load_dotenv("/var/www/neuralops/.env")
logger = logging.getLogger(__name__)

_client: tweepy.Client | None = None
_api_v1: tweepy.API | None = None


def is_configured() -> bool:
    return all([
        os.getenv("TWITTER_API_KEY"),
        os.getenv("TWITTER_API_SECRET"),
        os.getenv("TWITTER_ACCESS_TOKEN"),
        os.getenv("TWITTER_ACCESS_SECRET"),
    ])


def get_client() -> tweepy.Client:
    global _client
    if _client is None:
        _client = tweepy.Client(
            bearer_token=os.getenv("TWITTER_BEARER_TOKEN"),
            consumer_key=os.getenv("TWITTER_API_KEY"),
            consumer_secret=os.getenv("TWITTER_API_SECRET"),
            access_token=os.getenv("TWITTER_ACCESS_TOKEN"),
            access_token_secret=os.getenv("TWITTER_ACCESS_SECRET"),
            wait_on_rate_limit=True,
        )
    return _client


def get_api_v1() -> tweepy.API:
    """Twitter API v1.1 — needed for profile/banner updates."""
    global _api_v1
    if _api_v1 is None:
        auth = tweepy.OAuth1UserHandler(
            os.getenv("TWITTER_API_KEY"),
            os.getenv("TWITTER_API_SECRET"),
            os.getenv("TWITTER_ACCESS_TOKEN"),
            os.getenv("TWITTER_ACCESS_SECRET"),
        )
        _api_v1 = tweepy.API(auth, wait_on_rate_limit=True)
    return _api_v1


def post_tweet(text: str) -> dict:
    """Post a single tweet. Returns {'success': True, 'tweet_id': str} or {'success': False, 'error': str}."""
    try:
        client = get_client()
        response = client.create_tweet(text=text[:280])
        tweet_id = response.data["id"]
        logger.info(f"[TwitterClient] tweet publicado: {tweet_id}")
        return {"success": True, "tweet_id": tweet_id}
    except Exception as e:
        logger.error(f"[TwitterClient] error publicando: {e}")
        return {"success": False, "error": str(e)}


def post_thread(tweets: list[str]) -> dict:
    """Post a thread of tweets. Returns {'success': True, 'tweet_ids': list} or error."""
    try:
        client = get_client()
        tweet_ids = []
        reply_to = None

        for text in tweets:
            kwargs = {"text": text[:280]}
            if reply_to:
                kwargs["in_reply_to_tweet_id"] = reply_to
            response = client.create_tweet(**kwargs)
            tweet_id = response.data["id"]
            tweet_ids.append(tweet_id)
            reply_to = tweet_id

        logger.info(f"[TwitterClient] thread de {len(tweet_ids)} tweets publicado")
        return {"success": True, "tweet_ids": tweet_ids}
    except Exception as e:
        logger.error(f"[TwitterClient] error publicando thread: {e}")
        return {"success": False, "error": str(e)}
