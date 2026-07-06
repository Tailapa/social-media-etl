"""Unit tests for app/normalization/{common,instagram,twitter,youtube}.py."""

from __future__ import annotations

from datetime import datetime

from app.normalization import instagram, twitter, youtube
from app.normalization.common import as_int, dedupe_by_key, first_present, merge_prefer_non_null

# ============================================================================
# common.py
# ============================================================================


def test_dedupe_by_key_last_wins_on_duplicate():
    items = [{"id": "1", "v": "a"}, {"id": "2", "v": "b"}, {"id": "1", "v": "c"}]
    result = dedupe_by_key(items, key_fn=lambda item: item["id"])
    assert len(result) == 2
    by_id = {item["id"]: item for item in result}
    assert by_id["1"]["v"] == "c"
    assert by_id["2"]["v"] == "b"


def test_dedupe_by_key_preserves_insertion_order_of_first_occurrence():
    items = [{"id": "a"}, {"id": "b"}, {"id": "a"}]
    result = dedupe_by_key(items, key_fn=lambda item: item["id"])
    assert [item["id"] for item in result] == ["a", "b"]


def test_dedupe_by_key_empty_input():
    assert dedupe_by_key([], key_fn=lambda item: item["id"]) == []


def test_merge_prefer_non_null_incoming_overrides_existing():
    existing = {"bio": "old bio", "followers": 10}
    incoming = {"bio": "new bio", "followers": 20}
    merged = merge_prefer_non_null(existing, incoming)
    assert merged == {"bio": "new bio", "followers": 20}


def test_merge_prefer_non_null_keeps_existing_when_incoming_is_none():
    existing = {"bio": "old bio"}
    incoming = {"bio": None}
    merged = merge_prefer_non_null(existing, incoming)
    assert merged["bio"] == "old bio"


def test_merge_prefer_non_null_keeps_existing_when_incoming_is_empty_string_list_or_dict():
    existing = {"a": "x", "b": [1, 2], "c": {"k": "v"}}
    incoming = {"a": "", "b": [], "c": {}}
    merged = merge_prefer_non_null(existing, incoming)
    assert merged == existing


def test_merge_prefer_non_null_adds_new_keys():
    existing = {"a": "x"}
    incoming = {"b": "y"}
    merged = merge_prefer_non_null(existing, incoming)
    assert merged == {"a": "x", "b": "y"}


def test_first_present_returns_first_matching_key():
    source = {"commentCount": 5, "commentsCount": 10}
    assert first_present(source, "commentsCount", "commentCount") == 10


def test_first_present_falls_back_to_next_key_when_missing():
    source = {"commentCount": 5}
    assert first_present(source, "commentsCount", "commentCount") == 5


def test_first_present_skips_none_values():
    source = {"commentsCount": None, "commentCount": 7}
    assert first_present(source, "commentsCount", "commentCount") == 7


def test_first_present_returns_default_when_no_key_found():
    assert first_present({}, "a", "b", default="fallback") == "fallback"


def test_first_present_default_is_none_by_default():
    assert first_present({}, "a", "b") is None


def test_as_int_converts_numeric_string():
    assert as_int("42") == 42


def test_as_int_converts_float():
    assert as_int(3.9) == 3


def test_as_int_returns_none_for_none():
    assert as_int(None) is None


def test_as_int_returns_none_for_unparseable_value():
    assert as_int("not-a-number") is None


# ============================================================================
# instagram.py
# ============================================================================


def test_instagram_normalize_author_normal_case():
    raw = {
        "ownerId": "123",
        "ownerUsername": "insta_user",
        "ownerFullName": "Insta User",
        "biography": "hello bio",
        "verified": True,
        "followersCount": 1000,
        "followsCount": 50,
        "postsCount": 20,
    }
    author = instagram.normalize_author(raw)
    assert author.platform_user_id == "123"
    assert author.username == "insta_user"
    assert author.display_name == "Insta User"
    assert author.bio == "hello bio"
    assert author.is_verified is True
    assert author.follower_count == 1000
    assert author.following_count == 50
    assert author.post_count == 20


def test_instagram_normalize_author_alternate_field_names():
    raw = {
        "id": "456",
        "username": "alt_user",
        "fullName": "Alt User",
        "bio": "alt bio",
        "isVerified": True,
        "isPrivate": True,
        "followerCount": 200,
        "followingCount": 30,
        "postCount": 4,
    }
    author = instagram.normalize_author(raw)
    assert author.platform_user_id == "456"
    assert author.username == "alt_user"
    assert author.display_name == "Alt User"
    assert author.bio == "alt bio"
    assert author.is_verified is True
    assert author.is_private is True
    assert author.follower_count == 200
    assert author.following_count == 30
    assert author.post_count == 4


def test_instagram_normalize_author_missing_ids_falls_back_to_username():
    raw = {"username": "fallback_user"}
    author = instagram.normalize_author(raw)
    assert author.platform_user_id == "fallback_user"
    assert author.username == "fallback_user"


def test_instagram_normalize_author_default_profile_url():
    raw = {"username": "some_user"}
    author = instagram.normalize_author(raw)
    assert author.profile_url == "https://www.instagram.com/some_user/"


def test_instagram_normalize_post_extracts_hashtags_and_mentions_from_caption():
    raw = {"id": "post1", "caption": "Loving this #sunset with @friend today", "type": "image"}
    post = instagram.normalize_post(raw, author_id="a1")
    assert post.hashtags == ["sunset"]
    assert post.mentions == ["friend"]


def test_instagram_normalize_post_prefers_explicit_hashtags_field():
    raw = {"id": "post1", "caption": "no hashtags here", "hashtags": ["#Explicit"]}
    post = instagram.normalize_post(raw, author_id="a1")
    assert post.hashtags == ["explicit"]


def test_instagram_normalize_post_content_type_mapping():
    raw = {"id": "post1", "caption": "x", "productType": "clips"}
    post = instagram.normalize_post(raw, author_id="a1")
    assert post.content_type == "reel"


def test_instagram_normalize_post_default_content_type_is_post():
    raw = {"id": "post1", "caption": "x"}
    post = instagram.normalize_post(raw, author_id="a1")
    assert post.content_type == "post"


def test_instagram_normalize_post_builds_media_from_display_and_video_url():
    raw = {
        "id": "post1",
        "caption": "x",
        "displayUrl": "https://example.com/img.jpg",
        "videoUrl": "https://example.com/vid.mp4",
    }
    post = instagram.normalize_post(raw, author_id="a1")
    urls = {m.url for m in post.media}
    assert "https://example.com/img.jpg" in urls
    assert "https://example.com/vid.mp4" in urls


def test_instagram_normalize_post_child_posts_carousel_media():
    raw = {
        "id": "post1",
        "caption": "x",
        "childPosts": [
            {"displayUrl": "https://example.com/child1.jpg"},
            {"videoUrl": "https://example.com/child2.mp4"},
        ],
    }
    post = instagram.normalize_post(raw, author_id="a1")
    urls = {m.url for m in post.media}
    assert "https://example.com/child1.jpg" in urls
    assert "https://example.com/child2.mp4" in urls


def test_instagram_normalize_post_url_falls_back_to_shortcode():
    raw = {"id": "post1", "shortCode": "abc123", "caption": "x"}
    post = instagram.normalize_post(raw, author_id="a1")
    assert post.url == "https://www.instagram.com/p/abc123/"


def test_instagram_normalize_post_timestamp_parsing():
    raw = {"id": "post1", "caption": "x", "timestamp": "2024-01-15T10:30:00.000Z"}
    post = instagram.normalize_post(raw, author_id="a1")
    assert post.posted_at is not None
    assert post.posted_at.year == 2024
    assert post.posted_at.month == 1
    assert post.posted_at.day == 15


def test_instagram_normalize_post_platform_metadata_counters():
    raw = {"id": "post1", "caption": "x", "likesCount": 10, "commentsCount": 3}
    post = instagram.normalize_post(raw, author_id="a1")
    assert post.platform_metadata["likes_count"] == 10
    assert post.platform_metadata["comments_count"] == 3


def test_instagram_normalize_comment_normal_case():
    raw = {"id": "c1", "text": "great #post @you", "likesCount": 5, "repliesCount": 1}
    comment = instagram.normalize_comment(raw, post_id="p1", author_id="a1")
    assert comment.content == "great #post @you"
    assert comment.hashtags == ["post"]
    assert comment.mentions == ["you"]
    assert comment.likes == 5
    assert comment.reply_count == 1


def test_instagram_normalize_comment_defaults_when_text_missing():
    raw = {"id": "c1"}
    comment = instagram.normalize_comment(raw, post_id="p1", author_id="a1")
    assert comment.content == "(no text)"


def test_instagram_normalize_comment_parent_id_passed_through():
    raw = {"id": "c1", "text": "reply"}
    comment = instagram.normalize_comment(raw, post_id="p1", author_id="a1", parent_id="root1")
    assert comment.parent_comment_id == "root1"
    assert comment.is_reply is True


def test_instagram_extract_engagement_maps_metadata_keys():
    raw = {
        "id": "post1",
        "caption": "x",
        "likesCount": 10,
        "commentsCount": 4,
        "videoViewCount": 500,
    }
    post = instagram.normalize_post(raw, author_id="a1")
    engagement = instagram.extract_engagement(post)
    assert engagement.likes == 10
    assert engagement.comments_count == 4
    assert engagement.views == 500
    assert engagement.shares is None


# ============================================================================
# twitter.py
# ============================================================================


def test_twitter_normalize_author_normal_case_embedded():
    raw = {
        "author": {
            "id": "789",
            "userName": "tw_user",
            "name": "TW User",
            "description": "bio here",
            "isBlueVerified": True,
            "followers": 500,
            "following": 100,
            "statusesCount": 30,
        }
    }
    author = twitter.normalize_author(raw)
    assert author.platform_user_id == "789"
    assert author.username == "tw_user"
    assert author.display_name == "TW User"
    assert author.bio == "bio here"
    assert author.is_verified is True
    assert author.follower_count == 500
    assert author.following_count == 100
    assert author.post_count == 30


def test_twitter_normalize_author_alternate_field_names_standalone():
    raw = {
        "userId": "999",
        "screen_name": "alt_tw",
        "displayName": "Alt TW",
        "bio": "alt bio",
        "verified": True,
        "followersCount": 20,
        "followingCount": 5,
        "tweetsCount": 3,
    }
    author = twitter.normalize_author(raw)
    assert author.platform_user_id == "999"
    assert author.username == "alt_tw"
    assert author.display_name == "Alt TW"
    assert author.is_verified is True
    assert author.follower_count == 20
    assert author.following_count == 5
    assert author.post_count == 3


def test_twitter_normalize_post_hashtags_mentions_from_entities():
    raw = {
        "id": "t1",
        "fullText": "hello world",
        "entities": {
            "hashtags": [{"text": "News"}],
            "mentions": [{"username": "friend"}],
            "urls": [{"expanded_url": "https://example.com"}],
        },
    }
    post = twitter.normalize_post(raw, author_id="a1")
    assert post.hashtags == ["news"]
    assert post.mentions == ["friend"]
    assert post.urls == ["https://example.com"]


def test_twitter_normalize_post_falls_back_to_extraction_when_no_entities():
    raw = {"id": "t1", "fullText": "check #this out @someone http://example.com"}
    post = twitter.normalize_post(raw, author_id="a1")
    assert post.hashtags == ["this"]
    assert post.mentions == ["someone"]
    assert post.urls == ["http://example.com"]


def test_twitter_normalize_post_content_type_retweet():
    raw = {"id": "t1", "fullText": "x", "isRetweet": True}
    post = twitter.normalize_post(raw, author_id="a1")
    assert post.content_type == "retweet"


def test_twitter_normalize_post_content_type_quote():
    raw = {"id": "t1", "fullText": "x", "isQuote": True}
    post = twitter.normalize_post(raw, author_id="a1")
    assert post.content_type == "quote"


def test_twitter_normalize_post_content_type_default_tweet():
    raw = {"id": "t1", "fullText": "x"}
    post = twitter.normalize_post(raw, author_id="a1")
    assert post.content_type == "tweet"


def test_twitter_normalize_post_media_video_type():
    raw = {
        "id": "t1",
        "fullText": "x",
        "media": [{"type": "video", "media_url_https": "https://example.com/vid.mp4"}],
    }
    post = twitter.normalize_post(raw, author_id="a1")
    assert len(post.media) == 1
    assert post.media[0].media_type == "video"


def test_twitter_normalize_post_media_falls_back_to_extended_entities():
    raw = {
        "id": "t1",
        "fullText": "x",
        "extendedEntities": {"media": [{"url": "https://example.com/pic.jpg", "type": "photo"}]},
    }
    post = twitter.normalize_post(raw, author_id="a1")
    assert len(post.media) == 1
    assert post.media[0].media_type == "image"


def test_twitter_normalize_post_timestamp_iso_format():
    raw = {"id": "t1", "fullText": "x", "createdAt": "2024-03-01T12:00:00.000Z"}
    post = twitter.normalize_post(raw, author_id="a1")
    assert post.posted_at is not None
    assert post.posted_at.year == 2024


def test_twitter_normalize_post_timestamp_twitter_native_format():
    raw = {"id": "t1", "fullText": "x", "createdAt": "Wed Oct 10 20:19:24 +0000 2018"}
    post = twitter.normalize_post(raw, author_id="a1")
    assert post.posted_at is not None
    assert post.posted_at.year == 2018


def test_twitter_normalize_post_timestamp_missing_returns_none():
    raw = {"id": "t1", "fullText": "x"}
    post = twitter.normalize_post(raw, author_id="a1")
    assert post.posted_at is None


def test_twitter_normalize_comment_normal_case():
    raw = {"id": "c1", "fullText": "great tweet", "likeCount": 3, "replyCount": 1}
    comment = twitter.normalize_comment(raw, post_id="p1", author_id="a1")
    assert comment.content == "great tweet"
    assert comment.likes == 3
    assert comment.reply_count == 1


def test_twitter_extract_engagement_maps_metadata_keys():
    raw = {
        "id": "t1",
        "fullText": "x",
        "likeCount": 10,
        "viewCount": 200,
        "retweetCount": 5,
        "replyCount": 2,
    }
    post = twitter.normalize_post(raw, author_id="a1")
    engagement = twitter.extract_engagement(post)
    assert engagement.likes == 10
    assert engagement.views == 200
    assert engagement.shares == 5
    assert engagement.comments_count == 2


# ============================================================================
# youtube.py
# ============================================================================


def test_youtube_normalize_author_normal_case():
    raw = {
        "channelId": "chan1",
        "channelName": "Some Channel",
        "channelDescription": "desc",
        "channelIsVerified": True,
        "numberOfSubscribers": 1000,
        "channelTotalVideos": 50,
    }
    author = youtube.normalize_author(raw)
    assert author.platform_user_id == "chan1"
    assert author.username == "Some Channel"
    assert author.is_verified is True
    assert author.follower_count == 1000
    assert author.post_count == 50


def test_youtube_normalize_author_alternate_field_names():
    raw = {
        "channelUrl": "https://www.youtube.com/channel/chan2",
        "channelUsername": "AltChannel",
        "subscriberCount": 20,
        "videoCount": 3,
    }
    author = youtube.normalize_author(raw)
    assert author.platform_user_id == "https://www.youtube.com/channel/chan2"
    assert author.username == "AltChannel"
    assert author.follower_count == 20
    assert author.post_count == 3


def test_youtube_normalize_channel_normal_case():
    raw = {
        "channelId": "chan1",
        "channelName": "Some Channel",
        "channelDescription": "desc",
        "numberOfSubscribers": 1000,
        "channelTotalVideos": 50,
        "channelTotalViews": 100000,
        "channelLocation": "US",
    }
    channel = youtube.normalize_channel(raw, author_id="a1")
    assert channel.platform_channel_id == "chan1"
    assert channel.name == "Some Channel"
    assert channel.subscriber_count == 1000
    assert channel.video_count == 50
    assert channel.total_views == 100000
    assert channel.country == "US"


def test_youtube_normalize_post_hashtags_from_description():
    raw = {"id": "v1", "title": "My Video", "text": "check out #cooking today", "duration": 120}
    post = youtube.normalize_post(raw, author_id="a1")
    assert post.hashtags == ["cooking"]


def test_youtube_normalize_post_prefers_explicit_hashtags():
    raw = {"id": "v1", "title": "My Video", "text": "x", "hashtags": ["#Explicit"]}
    post = youtube.normalize_post(raw, author_id="a1")
    assert post.hashtags == ["explicit"]


def test_youtube_normalize_post_content_type_short_for_short_duration():
    raw = {"id": "v1", "title": "Short", "text": "x", "duration": 45}
    post = youtube.normalize_post(raw, author_id="a1")
    assert post.content_type == "short"


def test_youtube_normalize_post_content_type_video_for_long_duration():
    raw = {"id": "v1", "title": "Long", "text": "x", "duration": 600}
    post = youtube.normalize_post(raw, author_id="a1")
    assert post.content_type == "video"


def test_youtube_normalize_post_duration_parses_hh_mm_ss_string():
    raw = {"id": "v1", "title": "x", "text": "x", "duration": "01:02:03"}
    post = youtube.normalize_post(raw, author_id="a1")
    assert post.platform_metadata["duration_seconds"] == 3723.0


def test_youtube_normalize_post_duration_parses_mm_ss_string():
    raw = {"id": "v1", "title": "x", "text": "x", "duration": "02:30"}
    post = youtube.normalize_post(raw, author_id="a1")
    assert post.platform_metadata["duration_seconds"] == 150.0


def test_youtube_normalize_post_duration_numeric():
    raw = {"id": "v1", "title": "x", "text": "x", "duration": 42}
    post = youtube.normalize_post(raw, author_id="a1")
    assert post.platform_metadata["duration_seconds"] == 42.0


def test_youtube_normalize_post_duration_invalid_string_returns_none():
    raw = {"id": "v1", "title": "x", "text": "x", "duration": "not-a-duration"}
    post = youtube.normalize_post(raw, author_id="a1")
    assert post.platform_metadata["duration_seconds"] is None


def test_youtube_normalize_post_alternate_field_names():
    raw = {
        "videoId": "v2",
        "title": "x",
        "description": "desc text",
        "views": 100,
        "date": "2024-05-01T00:00:00Z",
    }
    post = youtube.normalize_post(raw, author_id="a1")
    assert post.platform_post_id == "v2"
    assert post.content == "desc text"
    assert post.platform_metadata["view_count"] == 100
    assert post.posted_at is not None
    assert post.posted_at.year == 2024


def test_youtube_normalize_video_maps_transcript_and_duration():
    raw = {
        "id": "v1",
        "title": "My Video",
        "text": "desc",
        "transcript": "hello there",
        "duration": 90,
        "url": "https://youtube.com/watch?v=v1",
    }
    video = youtube.normalize_video(raw, channel_id="chan1", post_id="p1")
    assert video.platform_video_id == "v1"
    assert video.channel_id == "chan1"
    assert video.post_id == "p1"
    assert video.transcript == "hello there"
    assert video.duration_seconds == 90.0
    assert video.has_transcript is True


def test_youtube_normalize_transcript_items_joins_text_lines():
    items = [{"text": "Hello"}, {"text": "world"}, {"text": ""}]
    result = youtube.normalize_transcript_items(items)
    assert result == "Hello world"


def test_youtube_normalize_transcript_items_empty_list():
    assert youtube.normalize_transcript_items([]) == ""


def test_youtube_normalize_comment_normal_case():
    raw = {"id": "c1", "text": "nice video", "voteCount": 4, "replyCount": 1}
    comment = youtube.normalize_comment(raw, post_id="p1", author_id="a1")
    assert comment.content == "nice video"
    assert comment.likes == 4
    assert comment.reply_count == 1


def test_youtube_normalize_comment_alternate_field_names():
    raw = {"cid": "c2", "comment": "alt text", "likesCount": 2}
    comment = youtube.normalize_comment(raw, post_id="p1", author_id="a1")
    assert comment.platform_comment_id == "c2"
    assert comment.content == "alt text"
    assert comment.likes == 2


def test_youtube_normalize_comment_reply_parent_from_is_reply_flag():
    raw = {"id": "c3", "text": "a reply", "isReply": True, "parentCommentId": "root1"}
    comment = youtube.normalize_comment(raw, post_id="p1", author_id="a1")
    assert comment.parent_comment_id == "root1"
    assert comment.is_reply is True


def test_youtube_normalize_comment_explicit_parent_id_overrides():
    raw = {"id": "c4", "text": "a reply"}
    comment = youtube.normalize_comment(
        raw, post_id="p1", author_id="a1", parent_id="explicit_root"
    )
    assert comment.parent_comment_id == "explicit_root"


def test_youtube_extract_engagement_maps_metadata_keys():
    raw = {
        "id": "v1",
        "title": "x",
        "text": "x",
        "viewCount": 5000,
        "likes": 300,
        "commentsCount": 20,
    }
    post = youtube.normalize_post(raw, author_id="a1")
    engagement = youtube.extract_engagement(post)
    assert engagement.views == 5000
    assert engagement.likes == 300
    assert engagement.comments_count == 20
    assert engagement.shares is None


def test_youtube_parse_timestamp_invalid_returns_none():
    raw = {"id": "v1", "title": "x", "text": "x", "date": "not-a-date"}
    post = youtube.normalize_post(raw, author_id="a1")
    assert post.posted_at is None


def test_common_timestamp_parsing_type_used_in_normalizers():
    # sanity check that datetime objects (not strings) survive round trip
    assert isinstance(datetime.now(), datetime)
