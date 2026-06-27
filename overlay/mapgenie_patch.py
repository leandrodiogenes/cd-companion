"""
Local MapGenie map.js patching for the desktop overlay.

The overlay keeps loading MapGenie itself, but redirects the CDN map.js request
to a local Qt scheme. The scheme handler downloads/cachees the original script
and applies a tiny source patch before Chromium executes it.
"""

import os
import sys
import urllib.parse
import urllib.request

from PyQt5.QtCore import QByteArray, QBuffer, QIODevice, QUrl
from PyQt5.QtWebEngineCore import (
    QWebEngineUrlRequestInterceptor,
    QWebEngineUrlScheme,
    QWebEngineUrlSchemeHandler,
)


PATCH_SCHEME = 'cdcompanion-mapjs'
MAPGENIE_MAP_JS_HOST = 'cdn.mapgenie.io'
MAPGENIE_MAP_JS_PATH = '/js/map.js'
# Ancora apenas na assinatura do metodo, que e estavel entre rebuilds da
# MapGenie. O corpo minificado (a variavel do lookup, ex: zn virou R) muda a
# cada build e por isso nao deve fazer parte da ancora. Injetamos logo apos a
# chave de abertura e deixamos o corpo original intacto.
PATCH_NEEDLE = 'renderCategory",value:function(e){'
PATCH_REPLACEMENT = (
    'renderCategory",value:function(e){'
    'window.__cdMapGeniePatch=window.__cdMapGeniePatch||{version:1,hooks:{}};'
    'window.__cdMapGeniePatch.categories=window.__cdMapGeniePatch.categories||{};'
    'window.__cdMapGeniePatch.categories.component=this;'
    'window.__cdMapGeniePatch.categories.props=this.props;'
    'window.__cdMapGeniePatch.categories.categoriesMap=this.props&&this.props.categoriesMap;'
    'window.__cdMapGeniePatch.categories.categoryGroups=this.props&&this.props.categoryGroups;'
    'window.__cdMapGeniePatch.categories.locationsByCategory=this.props&&this.props.locationsByCategory;'
    'window.__cdMapGeniePatch.categories.ready=!!(this.props&&this.props.categoriesMap);'
    'window.__cdMapGeniePatch.categories.updatedAt=Date.now();'
)


def register_mapgenie_patch_scheme():
    scheme = QWebEngineUrlScheme(PATCH_SCHEME.encode('ascii'))
    scheme.setSyntax(QWebEngineUrlScheme.Syntax.HostAndPort)
    scheme.setFlags(
        QWebEngineUrlScheme.SecureScheme |
        QWebEngineUrlScheme.LocalScheme |
        QWebEngineUrlScheme.LocalAccessAllowed |
        QWebEngineUrlScheme.ContentSecurityPolicyIgnored
    )
    QWebEngineUrlScheme.registerScheme(scheme)


def _cache_dir():
    base = os.environ.get('LOCALAPPDATA') or os.path.expanduser('~')
    path = os.path.join(base, 'CD_Teleport', 'mapgenie_cache')
    os.makedirs(path, exist_ok=True)
    return path


def _cache_path(source_url):
    parsed = urllib.parse.urlparse(source_url)
    query = urllib.parse.parse_qs(parsed.query)
    script_id = query.get('id', ['no-id'])[0]
    safe_id = ''.join(ch for ch in script_id if ch.isalnum() or ch in '-_') or 'no-id'
    return os.path.join(_cache_dir(), f'map-{safe_id}.js')


def _download_map_js(source_url):
    cache_path = _cache_path(source_url)
    if os.path.exists(cache_path):
        with open(cache_path, 'rb') as f:
            return f.read()

    req = urllib.request.Request(
        source_url,
        headers={
            'User-Agent': 'CD-Companion/overlay',
            'Accept': 'application/javascript,*/*;q=0.8',
        },
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = resp.read()

    with open(cache_path, 'wb') as f:
        f.write(data)
    return data


def _patch_map_js(data, source_url):
    try:
        text = data.decode('utf-8')
    except UnicodeDecodeError:
        return data, False

    count = text.count(PATCH_NEEDLE)
    if count != 1:
        print(f'[*] MapGenie map.js patch skipped: expected 1 match, found {count} ({source_url})')
        return data, False

    patched = text.replace(PATCH_NEEDLE, PATCH_REPLACEMENT, 1)
    if not getattr(sys, 'frozen', False):
        print(f'[*] MapGenie map.js patch applied ({source_url})')
    return patched.encode('utf-8'), True


class MapGenieMapJsInterceptor(QWebEngineUrlRequestInterceptor):
    def interceptRequest(self, info):
        url = info.requestUrl()
        if url.scheme() != 'https':
            return
        if url.host().lower() != MAPGENIE_MAP_JS_HOST:
            return
        if url.path() != MAPGENIE_MAP_JS_PATH:
            return

        encoded = urllib.parse.quote(url.toString(), safe='')
        info.redirect(QUrl(f'{PATCH_SCHEME}://map.js/?source={encoded}'))


class MapGenieMapJsSchemeHandler(QWebEngineUrlSchemeHandler):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._buffers = []

    def requestStarted(self, job):
        url = job.requestUrl()
        query = urllib.parse.parse_qs(url.query())
        source_url = query.get('source', [''])[0]
        if not source_url:
            job.fail(job.RequestDenied)
            return

        try:
            original = _download_map_js(source_url)
            data, _ = _patch_map_js(original, source_url)
        except Exception as exc:
            print(f'[*] MapGenie map.js patch failed: {exc}')
            job.fail(job.RequestFailed)
            return

        buf = QBuffer(job)
        buf.setData(QByteArray(data))
        buf.open(QIODevice.ReadOnly)
        self._buffers.append(buf)
        buf.destroyed.connect(lambda _=None, b=buf: self._forget_buffer(b))
        job.reply(QByteArray(b'application/javascript'), buf)

    def _forget_buffer(self, buf):
        try:
            self._buffers.remove(buf)
        except ValueError:
            pass


def install_mapgenie_patch(profile, parent=None):
    handler = MapGenieMapJsSchemeHandler(parent)
    interceptor = MapGenieMapJsInterceptor(parent)
    profile.installUrlSchemeHandler(PATCH_SCHEME.encode('ascii'), handler)
    profile.setRequestInterceptor(interceptor)
    return handler, interceptor
