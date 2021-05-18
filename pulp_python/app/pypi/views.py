import logging

from rest_framework.viewsets import ViewSet
from rest_framework.response import Response
from django.core.exceptions import ObjectDoesNotExist
from django.shortcuts import redirect
from django.http.response import Http404, StreamingHttpResponse
from drf_spectacular.utils import extend_schema
from dynaconf import settings
from urllib.parse import urljoin
from pathlib import PurePath

from pulp_python.app.models import (
    PythonDistribution,
    PythonPackageContent,
    PythonPublication,
)
from pulp_python.app.pypi.serializers import (
    SummarySerializer,
    PackageMetadataSerializer,
)
from pulp_python.app.utils import (
    write_simple_index,
    write_simple_detail,
    python_content_to_json,
    PYPI_LAST_SERIAL,
    PYPI_SERIAL_CONSTANT,
)

log = logging.getLogger(__name__)

BASE_CONTENT_URL = urljoin(settings.CONTENT_ORIGIN, settings.CONTENT_PATH_PREFIX)


class PyPIMixin:
    """Mixin to get index specific info."""

    @staticmethod
    def get_repository_version(distribution):
        """Finds the repository version this distribution is serving."""
        pub = distribution.publication
        rep = distribution.repository
        if pub:
            return pub.repository_version or pub.repository.latest_version()
        elif rep:
            return rep.latest_version()
        else:
            raise Http404("No repository associated with this index")

    @staticmethod
    def get_distribution(path):
        """Finds the distribution associated with this base_path."""
        distro_qs = PythonDistribution.objects.select_related(
            "repository", "publication", "publication__repository_version"
        )
        try:
            return distro_qs.get(base_path=path)
        except ObjectDoesNotExist:
            raise Http404(f"No PythonDistribution found for base_path {path}")

    @staticmethod
    def get_content(repository_version):
        """Returns queryset of the content in this repository version."""
        return PythonPackageContent.objects.filter(pk__in=repository_version.content)

    def should_redirect(self, distro, repo_version=None):
        """Checks if there is a publication the content app can serve."""
        if distro.publication:
            return True
        rv = repo_version or self.get_repository_version(distro)
        return PythonPublication.objects.filter(repository_version=rv).exists()

    def get_drvc(self, path):
        """Takes the base_path and returns the distribution, repository_version and content."""
        distro = self.get_distribution(path)
        repo_ver = self.get_repository_version(distro)
        content = self.get_content(repo_ver)
        return distro, repo_ver, content


class SimpleView(ViewSet, PyPIMixin):
    """View for the PyPI simple API."""

    authentication_classes = []
    permission_classes = []

    @extend_schema(summary="Get index simple page")
    def list(self, request, path):
        """Gets the simple api html page for the index."""
        distro, repo_version, content = self.get_drvc(path)
        if self.should_redirect(distro, repo_version=repo_version):
            return redirect(urljoin(BASE_CONTENT_URL, f'{path}/simple/'))
        names = content.order_by('name').values_list('name', flat=True).distinct().iterator()
        return StreamingHttpResponse(write_simple_index(names, streamed=True))

    @extend_schema(summary="Get package simple page")
    def retrieve(self, request, path, package):
        """Retrieves the simple api html page for a package."""
        distro, repo_ver, content = self.get_drvc(path)
        if self.should_redirect(distro, repo_version=repo_ver):
            # Maybe this name needs to be normalized?
            return redirect(urljoin(BASE_CONTENT_URL, f'{path}/simple/{package}/'))
        packages = content.filter(name__iexact=package).values_list('filename', 'sha256').iterator()
        detail_packages = ((f, urljoin(BASE_CONTENT_URL, f'{path}/{f}'), d) for f, d in packages)
        return StreamingHttpResponse(
            write_simple_detail(package, detail_packages, streamed=True)
        )


class MetadataView(ViewSet, PyPIMixin):
    """View for the PyPI JSON metadata endpoint."""

    authentication_classes = []
    permission_classes = []

    @extend_schema(tags=["Pypi: Metadata"],
                   responses={200: PackageMetadataSerializer},
                   summary="Get package metadata")
    def retrieve(self, request, path, meta):
        """
        Retrieves the package's core-metadata specified by
        https://packaging.python.org/specifications/core-metadata/.
        `meta` must be a path in form of `{package}/json/` or `{package}/{version}/json/`
        """
        distro, repo_ver, content = self.get_drvc(path)
        meta_path = PurePath(meta)
        name = None
        version = None
        if meta_path.match("*/*/json"):
            version = meta_path.parts[1]
            name = meta_path.parts[0]
        elif meta_path.match("*/json"):
            name = meta_path.parts[0]
        if name:
            package_content = content.filter(name__iexact=name)
            # TODO Change this value to the Repo's serial value when implemented
            headers = {PYPI_LAST_SERIAL: str(PYPI_SERIAL_CONSTANT)}
            json_body = python_content_to_json(path, package_content, version=version)
            if json_body:
                return Response(data=json_body, headers=headers)
        return Response(status="404")


class PyPIView(ViewSet, PyPIMixin):
    """View for base_url of distribution."""

    authentication_classes = []
    permission_classes = []

    @extend_schema(responses={200: SummarySerializer},
                   summary="Get index summary")
    def retrieve(self, request, path):
        """Gets package summary stats of index."""
        distro, repo_ver, content = self.get_drvc(path)
        files = content.count()
        releases = content.distinct("name", "version").count()
        projects = content.distinct("name").count()
        data = {"projects": projects, "releases": releases, "files": files}
        return Response(data=data)
