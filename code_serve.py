#!/usr/bin/python

# Copyright 2013 Clark DuVall
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import argparse
import CGIHTTPServer
import memcache
import os
import re
import SocketServer
import subprocess
import tempfile
import urllib
import urlparse

INCLUDE = ['.']
BASE_PATH = '.'
VIM_ARGS = []
CACHE = None
COLOR_DIR = '/usr/share/vim/vim73/colors/'

CSS = '''
<style type="text/css">
ul {
  list-style: none;
}

#pickerParent {
  overflow: hidden;
  position: fixed;
  padding-bottom: 100px;
  padding-left: 100px;
  right: 0;
  top: 0;
}

#picker * {
  font-size: 14px;
}

#picker {
  width: 110%;
  background: black;
  color: white;
  z-index: 1;
  position: relative;
  overflow: hidden;
  padding: 10px;
  right: -90%;
  -webkit-transition: right .5s, box-shadow .5s;
  -moz-transition: right .5s, box-shadow .5s;
  -o-transition: right .5s, box-shadow .5s;
  -ms-transition: right .5s, box-shadow .5s;
  transition: right .5s, box-shadow .5s;
}

#picker:hover {
  right: 10%;
  box-shadow: -10px 10px 50px #888;
}

#picker #expand {
  position: absolute;
  top: 0;
  left: 10%;
  width: 43%;
  padding-bottom: 10%;
  padding-top: 2%;
  text-align: center;
  background: #888;
  color: #000;
  font-size: 25px;
  margin: auto;
  -webkit-transition: background .5s, color .5s;
  -moz-transition: background .5s, color .5s;
  -o-transition: background .5s, color .5s;
  -ms-transition: background .5s, color .5s;
  transition: background .5s, color .5s;

  -webkit-transform: rotate(90deg);
  -moz-transform: rotate(90deg);
  -o-transform: rotate(90deg);
  -ms-transform: rotate(90deg);
  transform: rotate(90deg);

  -webkit-transform-origin: 0 0;
  -mox-transform-origin: 0 0;
  -o-transform-origin: 0 0;
  -ms-transform-origin: 0 0;
  transform-origin: 0 0;
}

#picker:hover #expand {
  background: rgba(0, 0, 0, 0);
  color: rgba(0, 0, 0, 0);
}

.label {
  float: right;
}

.option {
  font-size: 75%;
}

.size {
  width: 20%;
}

#submit {
}

.linkDiv {
  float: left;
  margin-top: 10px;
}

.linkDiv a {
  font-weight: bold;
}

.link {
  color: inherit;
}
</style>
'''

BACK_HTML = '''
<div>
  <a class="link" href="/%s%s">Up directory</a>
</div>
'''

COLOR_PICKER_HTML = '''
<div id="pickerParent"><div id="picker">
  <div id="expand">Options</div>
  <form>
    <table>
      <tr>
        <td class="label">Vim Color Scheme:</td>
        <td><select name="colorscheme" class="option">
          <option value="">(none)</option>
          %s
        </select></td>
      </tr>
      <tr>
        <td class="label">Background:</td>
        <td>
            <input %s type="radio" name="bg" value="dark">Dark
            <input %s type="radio" name="bg" value="light">Light
            <input %s type="radio" name="bg" value="">Default
        </td>
      </tr>
      <tr>
        <td class="label">Line Numbers:</td>
        <td>
            <input %s type="radio" name="nu" value="on">On
            <input %s type="radio" name="nu" value="off">Off
        </td>
      </tr>
      <tr>
        <td class="label">Font Size:</td>
        <td>
            <input class="option size"
                   type="number"
                   name="size"
                   value="%s"> px
        </td>
      </tr>
      <tr>
        <td></td>
        <td><input id="submit" type="submit" value="Refresh"></td>
      </tr>
    </table>
  </form>
  <div class="linkDiv">
      Powered by <a href="https://github.com/clark-duvall/CodeServe"
                    target="_blank",
                    class="link">CodeServe</a>.
  </div>
</div></div>
'''

LIST_DIR_HTML = '''
<!DOCTYPE html>
<html>
<body>
  <h2><span class="Constant">cwd:</span> <span class="Statement">%s/</span></h2>
  <ul>
    %s
  </ul>
</body>
</html>
'''

def _ReadFile(filename):
  with open(filename) as f:
    return f.read()

def _WriteFile(filename, contents):
  with open(filename, 'w') as f:
    return f.write(contents)

def _UrlExists(url, current=None):
  for include in INCLUDE:
    path = os.path.normpath(os.path.join(BASE_PATH, include, url))
    if os.path.exists(path):
      return (path, url)
  if current is not None:
    path = os.path.normpath(
        os.path.join(BASE_PATH, os.path.dirname(current), url))
    prefix = os.path.commonprefix([path, BASE_PATH])
    link_path = path.replace(prefix, '')
    if os.path.exists(path):
      return (path, link_path.replace(os.sep, '/'))
  return (None, None)

def _CheckPathReplace(match, opening, closing, path):
  _, link_path = _UrlExists(match.group(4), current=path)
  if link_path is not None:
    return ('<%s>#include </%s><%s>%s<a style="color: inherit" class="include" '
            'href="/%s">%s</a>%s' %
                (match.group(1), match.group(2), match.group(3),
                 opening, link_path, match.group(4), closing))
  return match.group(0)

def _LinkIncludes(html, path):
  # This will need to change if vim TOhtml ever changes.
  regex = r'<(.*?)>#include </(.*?)><(.*?)>%s(.*)%s'
  quot = '&quot;'
  lt = '&lt;'
  gt = '&gt;'
  subbed_html = re.sub(regex % (quot, quot),
                       lambda x: _CheckPathReplace(x, quot, quot, path),
                       html)
  return re.sub(regex % (lt, gt),
                lambda x: _CheckPathReplace(x, lt, gt, path),
                subbed_html)

def _InsertHtml(html, to_insert, before):
  parts = html.split(before)
  parts[0] = '%s%s' % (parts[0], before)
  parts.insert(1, to_insert)
  return ''.join(parts)

def _GetColorSchemeHtml(current):
  return ''.join('<option %s value="%s">%s</option>' %
      ('selected' if name[:-4] == current else '', name[:-4], name[:-4])
          for name in sorted(os.listdir(COLOR_DIR)) if name.endswith('.vim'))

def _LinkPathParts(path):
  normpath = os.path.normpath(path.replace(os.sep, '/'))
  parts = normpath.split('/')
  if parts[0] != '.':
    parts.insert(0, '.')
  return '/'.join('<a class="link" href="/%s/">%s</a>' %
      ('/'.join(parts[:i + 1]), part) for i, part in enumerate(parts))

class _VimQueryArgs(object):
  _VALID_COMMANDS = ['colorscheme']
  _VALID_OPTIONS = ['bg']
  _VALID_TOGGLES = ['nu']
  def __init__(self, query):
    self._query = dict((k, v[0]) for k, v in query.iteritems())

  def GetVimArgs(self):
    # Separate commands and options so commands can be done before options.
    commands = []
    options = []
    for name, arg in self._query.iteritems():
      if name in self._VALID_COMMANDS:
        commands.append('+%s %s' % (name, arg))
      if name in self._VALID_OPTIONS:
        options.append('+set %s=%s' % (name, arg))
      if name in self._VALID_TOGGLES:
        options.append('+set %s%s' % (name, '' if arg == 'on' else '!'))
    return commands + options

  def GetColorPickerHtml(self):
    return (COLOR_PICKER_HTML %
        (_GetColorSchemeHtml(self._query.get('colorscheme', '')),
         'checked' if self._query.get('bg', '') == 'dark' else '',
         'checked' if self._query.get('bg', '') == 'light' else '',
         'checked' if self._query.get('bg', '') == '' else '',
         'checked' if self._query.get('nu', '') == 'on' else '',
         'checked' if self._query.get('nu', '') == 'off' else '',
         self._query.get('size', '')))

  def GetBackHtml(self, path):
    link = os.path.dirname(path)
    return BACK_HTML % ('%s/' % link if len(link) else '', self.QueryString())

  def QueryString(self):
    return ('?%s' % urllib.urlencode(self._query).strip('/')
        if len(self._query) else '')

  def __getitem__(self, key):
    return self._query.get(key, '')

  def __str__(self):
    return str(sorted(filter(lambda x: len(x[1]), self._query.iteritems())))


class _Cache(object):
  def __init__(self, no_cache):
    if no_cache:
      self._memcache = None
    else:
      self._memcache = memcache.Client(['127.0.0.1:11211'])

  def Get(self, key):
    if self._memcache is None:
      return None
    return self._memcache.get(key.replace(' ', ''))

  def Set(self, key, value):
    if self._memcache is not None:
      self._memcache.set(key.replace(' ', ''), value, time=3600)

def _AddQueryToLinks(html, prefix, query):
  return re.sub(r'%shref="(.*?)"' % prefix,
                r'%shref="\1%s"' % (prefix, query),
                html)

class Handler(CGIHTTPServer.CGIHTTPRequestHandler):
  def _CallVim(self, path, query_args, extra_args=None):
    fd, name = tempfile.mkstemp()
    swap = os.path.join(os.path.dirname(path),
                        '.%s.swp' % os.path.basename(path))
    if os.path.exists(swap):
      os.remove(swap)
    vim = ['vim', path]
    vim.extend(['+%s' % arg for arg in VIM_ARGS])
    vim.extend(query_args.GetVimArgs())
    if extra_args is not None:
      vim.extend(extra_args)
    vim.extend(['+TOhtml','+w! %s' % name, '+qa!'])
    try:
      subprocess.check_call(vim)
    except subprocess.CalledProcessError as e:
      self.send_error(500, 'Vim error: %s' % e)
      return None
    with os.fdopen(fd) as f:
      html = f.read()
    os.remove(name)
    return html

  def _SendHtmlFile(self, path, url, query_args):
    cache_path = '%s%s' % (path, query_args)
    html = CACHE.Get(cache_path)
    if html is None:
      html = self._CallVim(path, query_args)
      if html is None:
        return
      html = _InsertHtml(html, query_args.GetColorPickerHtml(), '<body>')
      html = _InsertHtml(html, query_args.GetBackHtml(url), '<body>')
      html = _InsertHtml(html, CSS, '<head>')
      html = _LinkIncludes(html, path)
      CACHE.Set(cache_path, html)

    self.send_response(200)
    self.send_header('Content-type', 'text/html')
    self.end_headers()
    if query_args['size']:
      html = re.sub(r'font-size: 1em;',
                    'font-size: %spx;' % query_args['size'],
                    html)
    self.wfile.write(_AddQueryToLinks(html,
                                      'class="include" ',
                                      query_args.QueryString()))

  def _ExtractCSS(self, query_args):
    fd, name = tempfile.mkstemp()
    # Write a keyword and a constant so the CSS is generated.
    with os.fdopen(fd, 'w') as f:
      f.write('for ""\n#define')
    html = self._CallVim(name, query_args, extra_args=['+setf cpp'])
    os.remove(name)
    first = html.find('<style type="text/css">')
    second = html.find('</style>')
    return html[first:second + len('</style>')]

  def _ListDirectory(self, path, url):
    paths = []
    for name in sorted(os.listdir(path)):
      file_url = '/'.join([url, name])
      if os.path.isdir(os.path.join(path, name)):
        paths.append('<li><a class="PreProc" href="/%s/">%s</a></li>' %
            (file_url, name))
      else:
        paths.append('<li><a class="link" href="/%s">%s</a></li>' %
            (file_url, name))
    return LIST_DIR_HTML % (_LinkPathParts(url), ''.join(paths))

  def _SendHtmlDirectory(self, path, url, query_args):
    cache_path = '%s%s' % (path, query_args)
    listing = CACHE.Get(cache_path)
    if listing is None:
      listing = _AddQueryToLinks(
          self._ListDirectory(path, url), '', query_args.QueryString())
      listing = _InsertHtml(listing, query_args.GetColorPickerHtml(), '<body>')
      listing = _InsertHtml(listing, query_args.GetBackHtml(url), '<body>')
      listing = _InsertHtml(listing, CSS, '<head>')
      listing = _InsertHtml(listing, self._ExtractCSS(query_args), '<head>')
      CACHE.Set(cache_path, listing)
    self.send_response(200)
    self.send_header('Content-type', 'text/html')
    self.end_headers()
    if query_args['size']:
      listing = re.sub(r'font-size: 1em;',
                       'font-size: %spx;' % query_args['size'],
                       listing)
    self.wfile.write(listing)

  def do_GET(self):
    parse_result = urlparse.urlparse(self.path)
    query_args = _VimQueryArgs(urlparse.parse_qs(parse_result.query))
    url = parse_result.path.strip('/')
    if not len(url):
      url = '.'
    path, _ = _UrlExists(url)
    if path is None:
      self.send_error(404, 'Path does not exist :(')
      return
    if os.path.isdir(path):
      if parse_result.path[-1:] != '/':
        self.send_response(301)
        self.send_header('Location', '%s/?%s' %
            (path.replace(os.sep, '/'), query_args.QueryString()))
        self.end_headers()
      else:
        self._SendHtmlDirectory(path, url, query_args)
    else:
      self._SendHtmlFile(path, url, query_args)

class Server(SocketServer.TCPServer):
  allow_reuse_address = True

if __name__ == '__main__':
  parser = argparse.ArgumentParser()
  parser.add_argument('-i', '--include', nargs='+',
                      help='include paths to use when searching for code, '
                           'relative to the base path')
  parser.add_argument('-b', '--base-path', default=BASE_PATH,
                      help='the base path to serve code from')
  parser.add_argument('-p', '--port', default=8000, type=int,
                      help='the port to run the server on')
  parser.add_argument('-v', '--vim-args', nargs='+', default=[],
                      help='extra arguments to pass to vim')
  parser.add_argument('-c', '--color-dir', default=COLOR_DIR,
                      help='the directory to find vim color schemes')
  parser.add_argument('--no-cache', default=False, action='store_true',
                      help='prevent caching of the pages')
  args = parser.parse_args()
  if args.include:
    INCLUDE.extend(args.include)
  BASE_PATH = '%s/' % os.path.normpath(args.base_path)
  VIM_ARGS = args.vim_args
  CACHE = _Cache(args.no_cache)
  COLOR_DIR = args.color_dir
  print('Go to http://localhost:%d to view your source.' % args.port)

  Server(('', args.port), Handler).serve_forever()
