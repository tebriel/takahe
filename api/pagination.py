import dataclasses
import urllib.parse
from collections.abc import Callable
from typing import Any

from django.db import models
from django.http import HttpRequest

from activities.models import PostInteraction


@dataclasses.dataclass
class PaginationResult:
    """
    Represents a pagination result for Mastodon (it does Link header stuff)
    """

    #: A list of objects that matched the pagination query.
    results: list[models.Model]

    #: The actual applied limit, which may be different from what was requested.
    limit: int

    #: A list of transformed JSON objects
    json_results: list[dict] | None = None

    @classmethod
    def empty(cls):
        return cls(results=[], limit=20)

    def next(self, request: HttpRequest, allowed_params: list[str]):
        """
        Returns a URL to the next page of results.
        """
        if not self.results:
            return None
        if self.json_results is None:
            raise ValueError("You must JSONify the results first")
        params = self.filter_params(request, allowed_params)
        params["max_id"] = self.json_results[-1]["id"]

        return f"{request.build_absolute_uri(request.path)}?{urllib.parse.urlencode(params)}"

    def prev(self, request: HttpRequest, allowed_params: list[str]):
        """
        Returns a URL to the previous page of results.
        """
        if not self.results:
            return None
        if self.json_results is None:
            raise ValueError("You must JSONify the results first")
        params = self.filter_params(request, allowed_params)
        params["min_id"] = self.json_results[0]["id"]

        return f"{request.build_absolute_uri(request.path)}?{urllib.parse.urlencode(params)}"

    def link_header(self, request: HttpRequest, allowed_params: list[str]):
        """
        Creates a link header for the given request
        """
        return ", ".join(
            (
                f'<{self.next(request, allowed_params)}>; rel="next"',
                f'<{self.prev(request, allowed_params)}>; rel="prev"',
            )
        )

    def jsonify_results(self, map_function: Callable[[Any], Any]):
        """
        Replaces our results with ones transformed via map_function
        """
        self.json_results = [map_function(result) for result in self.results]

    def jsonify_posts(self, identity):
        """
        Predefined way of JSON-ifying Post objects
        """
        interactions = PostInteraction.get_post_interactions(self.results, identity)
        self.jsonify_results(
            lambda post: post.to_mastodon_json(interactions=interactions)
        )

    def jsonify_status_events(self, identity):
        """
        Predefined way of JSON-ifying TimelineEvent objects representing statuses
        """
        interactions = PostInteraction.get_event_interactions(self.results, identity)
        self.jsonify_results(
            lambda event: event.to_mastodon_status_json(interactions=interactions)
        )

    def jsonify_notification_events(self, identity):
        """
        Predefined way of JSON-ifying TimelineEvent objects representing notifications
        """
        interactions = PostInteraction.get_event_interactions(self.results, identity)
        self.jsonify_results(
            lambda event: event.to_mastodon_notification_json(interactions=interactions)
        )

    def jsonify_identities(self):
        """
        Predefined way of JSON-ifying Identity objects
        """
        self.jsonify_results(lambda identity: identity.to_mastodon_json())

    @staticmethod
    def filter_params(request: HttpRequest, allowed_params: list[str]):
        params = {}
        for key in allowed_params:
            value = request.GET.get(key, None)
            if value:
                params[key] = value
        return params


class MastodonPaginator:
    """
    Paginates in the Mastodon style (max_id, min_id, etc).
    Note that this basically _requires_ us to always do it on IDs, so we do.
    """

    def __init__(
        self,
        default_limit: int = 20,
        max_limit: int = 40,
    ):
        self.default_limit = default_limit
        self.max_limit = max_limit

    def paginate(
        self,
        queryset,
        min_id: str | None,
        max_id: str | None,
        since_id: str | None,
        limit: int | None,
    ) -> PaginationResult:
        if max_id:
            queryset = queryset.filter(id__lt=max_id)

        if since_id:
            queryset = queryset.filter(id__gt=since_id)

        if min_id:
            # Min ID requires items _immediately_ newer than specified, so we
            # invert the ordering to accommodate
            queryset = queryset.filter(id__gt=min_id).order_by("id")
        else:
            queryset = queryset.order_by("-id")

        limit = min(limit or self.default_limit, self.max_limit)
        return PaginationResult(
            results=list(queryset[:limit]),
            limit=limit,
        )

    def paginate_home(
        self,
        queryset,
        min_id: str | None,
        max_id: str | None,
        since_id: str | None,
        limit: int | None,
    ) -> PaginationResult:
        """
        The home timeline requires special handling where we mix Posts and
        PostInteractions together.
        """
        if max_id:
            queryset = queryset.filter(
                models.Q(subject_post_id__lt=max_id)
                | models.Q(subject_post_interaction_id__lt=max_id)
            )

        if since_id:
            queryset = queryset.filter(
                models.Q(subject_post_id__gt=max_id)
                | models.Q(subject_post_interaction_id__gt=max_id)
            )

        if min_id:
            # Min ID requires items _immediately_ newer than specified, so we
            # invert the ordering to accommodate
            queryset = queryset.filter(
                models.Q(subject_post_id__gt=max_id)
                | models.Q(subject_post_interaction_id__gt=max_id)
            ).order_by("id")
        else:
            queryset = queryset.order_by("-id")

        limit = min(limit or self.default_limit, self.max_limit)
        return PaginationResult(
            results=list(queryset[:limit]),
            limit=limit,
        )
