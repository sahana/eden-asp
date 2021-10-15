# -*- coding: utf-8 -*-

""" Extensible Generic CRUD Controller

    @copyright: 2009-2021 (c) Sahana Software Foundation
    @license: MIT

    Permission is hereby granted, free of charge, to any person
    obtaining a copy of this software and associated documentation
    files (the "Software"), to deal in the Software without
    restriction, including without limitation the rights to use,
    copy, modify, merge, publish, distribute, sublicense, and/or sell
    copies of the Software, and to permit persons to whom the
    Software is furnished to do so, subject to the following
    conditions:

    The above copyright notice and this permission notice shall be
    included in all copies or substantial portions of the Software.

    THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
    EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES
    OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
    NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT
    HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY,
    WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
    FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR
    OTHER DEALINGS IN THE SOFTWARE.
"""

__all__ = ("S3Request",
           "s3_request",
           )

import json
import os
import re
import sys

from io import StringIO
from urllib.request import urlopen

from gluon import current, redirect, HTTP, URL
from gluon.storage import Storage

from .model import S3Resource
from .tools import s3_parse_datetime, s3_get_extension, s3_keep_messages, s3_remove_last_record_id, s3_store_last_record_id, s3_str

REGEX_FILTER = re.compile(r".+\..+|.*\(.+\).*")
HTTP_METHODS = ("GET", "PUT", "POST", "DELETE")

# =============================================================================
class S3Request(object):
    """
        Class to handle RESTful requests
    """

    INTERACTIVE_FORMATS = ("html", "iframe", "popup", "dl")
    DEFAULT_REPRESENTATION = "html"

    # -------------------------------------------------------------------------
    def __init__(self,
                 prefix = None,
                 name = None,
                 r = None,
                 c = None,
                 f = None,
                 args = None,
                 vars = None,
                 extension = None,
                 get_vars = None,
                 post_vars = None,
                 http = None):
        """
            Constructor

            @param prefix: the table name prefix
            @param name: the table name
            @param c: the controller prefix
            @param f: the controller function
            @param args: list of request arguments
            @param vars: dict of request variables
            @param extension: the format extension (representation)
            @param get_vars: the URL query variables (overrides vars)
            @param post_vars: the POST variables (overrides vars)
            @param http: the HTTP method (GET, PUT, POST, or DELETE)

            @note: all parameters fall back to the attributes of the
                   current web2py request object
        """

        auth = current.auth

        # Common settings

        # XSLT Paths
        self.XSLT_PATH = "static/formats"
        self.XSLT_EXTENSION = "xsl"

        # Attached files
        self.files = Storage()

        # Allow override of controller/function
        self.controller = c or self.controller
        self.function = f or self.function
        if "." in self.function:
            self.function, ext = self.function.split(".", 1)
            if extension is None:
                extension = ext
        if c or f:
            if not auth.permission.has_permission("read",
                                                  c=self.controller,
                                                  f=self.function):
                auth.permission.fail()

        # Allow override of request args/vars
        if args is not None:
            if isinstance(args, (list, tuple)):
                self.args = args
            else:
                self.args = [args]
        if get_vars is not None:
            self.get_vars = get_vars
            self.vars = get_vars.copy()
            if post_vars is not None:
                self.vars.update(post_vars)
            else:
                self.vars.update(self.post_vars)
        if post_vars is not None:
            self.post_vars = post_vars
            if get_vars is None:
                self.vars = post_vars.copy()
                self.vars.update(self.get_vars)
        if get_vars is None and post_vars is None and vars is not None:
            self.vars = vars
            self.get_vars = vars
            self.post_vars = Storage()

        self.extension = extension or current.request.extension
        self.http = http or current.request.env.request_method

        # Main resource attributes
        if r is not None:
            if not prefix:
                prefix = r.prefix
            if not name:
                name = r.name
        self.prefix = prefix or self.controller
        self.name = name or self.function

        # Parse the request
        self.__parse()
        self.custom_action = None
        get_vars = Storage(self.get_vars)

        # Interactive representation format?
        self.interactive = self.representation in self.INTERACTIVE_FORMATS

        # Show information on deleted records?
        include_deleted = False
        if self.representation == "xml" and "include_deleted" in get_vars:
            include_deleted = True
        if "components" in get_vars:
            cnames = get_vars["components"]
            if isinstance(cnames, list):
                cnames = ",".join(cnames)
            cnames = cnames.split(",")
            if len(cnames) == 1 and cnames[0].lower() == "none":
                cnames = []
        else:
            cnames = None

        # Append component ID to the URL query
        component_name = self.component_name
        component_id = self.component_id
        if component_name and component_id:
            varname = "%s.id" % component_name
            if varname in get_vars:
                var = get_vars[varname]
                if not isinstance(var, (list, tuple)):
                    var = [var]
                var.append(component_id)
                get_vars[varname] = var
            else:
                get_vars[varname] = component_id

        # Define the target resource
        _filter = current.response.s3.filter
        components = component_name
        if components is None:
            components = cnames

        tablename = "%s_%s" % (self.prefix, self.name)

        if not current.deployment_settings.get_auth_record_approval():
            # Record Approval is off
            approved, unapproved = True, False
        elif self.method == "review":
            approved, unapproved = False, True
        elif auth.s3_has_permission("review", tablename, self.id):
            # Approvers should be able to edit records during review
            # @ToDo: deployment_setting to allow Filtering out from
            #        multi-record methods even for those with Review permission
            approved, unapproved = True, True
        else:
            approved, unapproved = True, False

        self.resource = S3Resource(tablename,
                                   id = self.id,
                                   filter = _filter,
                                   vars = get_vars,
                                   components = components,
                                   approved = approved,
                                   unapproved = unapproved,
                                   include_deleted = include_deleted,
                                   context = True,
                                   filter_component = component_name,
                                   )

        self.tablename = self.resource.tablename
        table = self.table = self.resource.table

        # Try to load the master record
        self.record = None
        uid = self.vars.get("%s.uid" % self.name)
        if self.id or uid and not isinstance(uid, (list, tuple)):
            # Single record expected
            self.resource.load()
            if len(self.resource) == 1:
                self.record = self.resource.records().first()
                _id = table._id.name
                self.id = self.record[_id]
                s3_store_last_record_id(self.tablename, self.id)
            else:
                raise KeyError(current.ERROR.BAD_RECORD)

        # Identify the component
        self.component = None
        if self.component_name:
            c = self.resource.components.get(self.component_name)
            if c:
                self.component = c
            else:
                error = "%s not a component of %s" % (self.component_name,
                                                      self.resource.tablename)
                raise AttributeError(error)

        # Identify link table and link ID
        self.link = None
        self.link_id = None

        if self.component is not None:
            self.link = self.component.link
        if self.link and self.id and self.component_id:
            self.link_id = self.link.link_id(self.id, self.component_id)
            if self.link_id is None:
                raise KeyError(current.ERROR.BAD_RECORD)

        # Store method handlers
        self._handlers = {}
        set_handler = self.set_handler

        set_handler("export_tree", self.get_tree,
                    http=("GET",), transform=True)
        set_handler("import_tree", self.put_tree,
                    http=("GET", "PUT", "POST"), transform=True)
        set_handler("fields", self.get_fields,
                    http=("GET",), transform=True)
        set_handler("options", self.get_options,
                    http=("GET",),
                    representation = ("__transform__", "json"),
                    )

        sync = current.sync
        set_handler("sync", sync,
                    http=("GET", "PUT", "POST",), transform=True)
        set_handler("sync_log", sync.log,
                    http=("GET",), transform=True)
        set_handler("sync_log", sync.log,
                    http=("GET",), transform=False)

        # Initialize CRUD
        self.resource.crud(self, method="_init")
        if self.component is not None:
            self.component.crud(self, method="_init")

    # -------------------------------------------------------------------------
    # Method handler configuration
    # -------------------------------------------------------------------------
    def set_handler(self, method, handler,
                    http = None,
                    representation = None,
                    transform = False):
        """
            Set a method handler for this request

            @param method: the method name
            @param handler: the handler function
            @type handler: handler(S3Request, **attr)
            @param http: restrict to these HTTP methods, list|tuple
            @param representation: register handler for non-transformable data
                                   formats
            @param transform: register handler for transformable data formats
                              (overrides representation)
        """

        if http is None:
            http = HTTP_METHODS
        else:
            if not isinstance(http, (tuple, list)):
                http = (http,)

        if transform:
            representation = ("__transform__",)
        elif not representation:
            representation = (self.DEFAULT_REPRESENTATION,)
        else:
            if not isinstance(representation, (tuple, list)):
                representation = (representation,)

        if not isinstance(method, (tuple, list)):
            method = (method,)

        handlers = self._handlers
        for h in http:
            if h not in HTTP_METHODS:
                continue
            format_hooks = handlers.get(h)
            if format_hooks is None:
                format_hooks = handlers[h] = {}
            for r in representation:
                method_hooks = format_hooks.get(r)
                if method_hooks is None:
                    method_hooks = format_hooks[r] = {}
                for m in method:
                    method_hooks[m] = handler

    # -------------------------------------------------------------------------
    def get_handler(self, method, transform=False):
        """
            Get a method handler for this request

            @param method: the method name
            @param transform: get handler for transformable data format

            @return: the method handler
        """

        handlers = self._handlers

        http_hooks = handlers.get(self.http)
        if not http_hooks:
            return None

        DEFAULT_REPRESENTATION = self.DEFAULT_REPRESENTATION
        hooks = http_hooks.get(DEFAULT_REPRESENTATION)
        if hooks:
            method_hooks = dict(hooks)
        else:
            method_hooks = {}

        representation = "__transform__" if transform else self.representation
        if representation and representation != DEFAULT_REPRESENTATION:
            hooks = http_hooks.get(representation)
            if hooks:
                method_hooks.update(hooks)

        if not method:
            methods = (None,)
        else:
            methods = (method, None)
        for m in methods:
            handler = method_hooks.get(m)
            if handler is not None:
                break

        if isinstance(handler, type):
            return handler()
        else:
            return handler

    # -------------------------------------------------------------------------
    def get_widget_handler(self, method):
        """
            Get the widget handler for a method

            @param r: the S3Request
            @param method: the widget method
        """

        if self.component:
            resource = self.component
            if resource.link:
                resource = resource.link
        else:
            resource = self.resource
        prefix, name = self.prefix, self.name
        component_name = self.component_name

        custom_action = current.s3db.get_method(self.tablename,
                                                component = component_name,
                                                method = method,
                                                )

        http = self.http
        handler = None

        if method and custom_action:
            handler = custom_action

        if http == "GET":
            if not method:
                if resource.count() == 1:
                    method = "read"
                else:
                    method = "list"
            transform = self.transformable()
            handler = self.get_handler(method, transform=transform)

        elif http == "PUT":
            transform = self.transformable(method="import")
            handler = self.get_handler(method, transform=transform)

        elif http == "POST":
            transform = self.transformable(method="import")
            return self.get_handler(method, transform=transform)

        elif http == "DELETE":
            if method:
                return self.get_handler(method)
            else:
                return self.get_handler("delete")

        else:
            return None

        if handler is None:
            handler = resource.crud
        if isinstance(handler, type):
            handler = handler()
        return handler

    # -------------------------------------------------------------------------
    # Request Parser
    # -------------------------------------------------------------------------
    def __parse(self):
        """ Parses the web2py request object """

        self.id = None
        self.component_name = None
        self.component_id = None
        self.method = None

        # Get the names of all components
        tablename = "%s_%s" % (self.prefix, self.name)

        # Map request args, catch extensions
        f = []
        append = f.append
        args = self.args
        if len(args) > 4:
            args = args[:4]
        method = self.name
        for arg in args:
            if "." in arg:
                arg, representation = arg.rsplit(".", 1)
            if method is None:
                method = arg
            elif arg.isdigit():
                append((method, arg))
                method = None
            else:
                append((method, None))
                method = arg
        if method:
            append((method, None))

        self.id = f[0][1]

        # Sort out component name and method
        l = len(f)
        if l > 1:
            m = f[1][0].lower()
            i = f[1][1]
            components = current.s3db.get_components(tablename, names=[m])
            if components and m in components:
                self.component_name = m
                self.component_id = i
            else:
                self.method = m
                if not self.id:
                    self.id = i
        if self.component_name and l > 2:
            self.method = f[2][0].lower()
            if not self.component_id:
                self.component_id = f[2][1]

        representation = s3_get_extension(self)
        if representation:
            self.representation = representation
        else:
            self.representation = self.DEFAULT_REPRESENTATION

        # Check for special URL variable $search, indicating
        # that the request body contains filter queries:
        if self.http == "POST" and "$search" in self.get_vars:
            self.__search()

    # -------------------------------------------------------------------------
    def __search(self):
        """
            Process filters in POST, interprets URL filter expressions
            in POST vars (if multipart), or from JSON request body (if
            not multipart or $search=ajax).

            NB: overrides S3Request method as GET (r.http) to trigger
                the correct method handlers, but will not change
                current.request.env.request_method
        """

        get_vars = self.get_vars
        content_type = self.env.get("content_type") or ""

        mode = get_vars.get("$search")

        # Override request method
        if mode:
            self.http = "GET"

        # Retrieve filters from request body
        if content_type == "application/x-www-form-urlencoded":
            # Read POST vars (e.g. from S3.gis.refreshLayer)
            filters = self.post_vars
            decode = None
        elif mode == "ajax" or content_type[:10] != "multipart/":
            # Read body JSON (e.g. from $.searchS3)
            body = self.body
            body.seek(0)
            # Decode request body (=bytes stream) into a str
            # - json.load/loads do not accept bytes in Py3 before 3.6
            # - minor performance advantage by avoiding the need for
            #   json.loads to detect the encoding
            s = body.read().decode("utf-8")
            try:
                filters = json.loads(s)
            except ValueError:
                filters = {}
            if not isinstance(filters, dict):
                filters = {}
            decode = None
        else:
            # Read POST vars JSON (e.g. from $.searchDownloadS3)
            filters = self.post_vars
            decode = json.loads

        # Move filters into GET vars
        get_vars = Storage(get_vars)
        post_vars = Storage(self.post_vars)

        del get_vars["$search"]
        for k, v in filters.items():
            k0 = k[0]
            if k == "$filter" or k[0:2] == "$$" or k == "bbox" or \
               k0 != "_" and ("." in k or k0 == "(" and ")" in k):
                try:
                    value = decode(v) if decode else v
                except ValueError:
                    continue
                # Catch any non-str values
                if type(value) is list:
                    value = [s3_str(item)
                             if not isinstance(item, str) else item
                             for item in value
                             ]
                elif type(value) is not str:
                    value = s3_str(value)
                get_vars[s3_str(k)] = value
                # Remove filter expression from POST vars
                if k in post_vars:
                    del post_vars[k]

        # Override self.get_vars and self.post_vars
        self.get_vars = get_vars
        self.post_vars = post_vars

        # Update combined vars
        self.vars = get_vars.copy()
        self.vars.update(self.post_vars)

    # -------------------------------------------------------------------------
    # REST Interface
    # -------------------------------------------------------------------------
    def __call__(self, **attr):
        """
            Execute this request

            @param attr: Parameters for the method handler
        """

        response = current.response
        s3 = response.s3
        self.next = None

        bypass = False
        output = None
        preprocess = None
        postprocess = None

        representation = self.representation

        # Enforce primary record ID
        if not self.id and representation == "html":
            if self.component or self.method in ("read", "profile", "update"):
                count = self.resource.count()
                if self.vars is not None and count == 1:
                    self.resource.load()
                    self.record = self.resource._rows[0]
                    self.id = self.record.id
                else:
                    #current.session.error = current.ERROR.BAD_RECORD
                    redirect(URL(r=self, c=self.prefix, f=self.name))

        # Pre-process
        if s3 is not None:
            preprocess = s3.get("prep")
        if preprocess:
            pre = preprocess(self)
            # Re-read representation after preprocess:
            representation = self.representation
            if pre and isinstance(pre, dict):
                bypass = pre.get("bypass", False) is True
                output = pre.get("output")
                if not bypass:
                    success = pre.get("success", True)
                    if not success:
                        if representation == "html" and output:
                            if isinstance(output, dict):
                                output["r"] = self
                            return output
                        else:
                            status = pre.get("status", 400)
                            message = pre.get("message",
                                              current.ERROR.BAD_REQUEST)
                            self.error(status, message)
            elif not pre:
                self.error(400, current.ERROR.BAD_REQUEST)

        # Default view
        if representation not in ("html", "popup"):
            response.view = "xml.html"

        # Content type
        response.headers["Content-Type"] = s3.content_type.get(representation,
                                                               "text/html")

        # Custom action?
        if not self.custom_action:
            action = current.s3db.get_method(self.tablename,
                                             component = self.component_name,
                                             method = self.method,
                                             )
            if isinstance(action, type):
                self.custom_action = action()
            else:
                self.custom_action = action

        # Method handling
        http = self.http
        handler = None
        if not bypass:
            # Find the method handler
            if self.method and self.custom_action:
                handler = self.custom_action
            elif http == "GET":
                handler = self.__GET()
            elif http == "PUT":
                handler = self.__PUT()
            elif http == "POST":
                handler = self.__POST()
            elif http == "DELETE":
                handler = self.__DELETE()
            else:
                self.error(405, current.ERROR.BAD_METHOD)
            # Invoke the method handler
            if handler is not None:
                output = handler(self, **attr)
            else:
                # Fall back to CRUD
                output = self.resource.crud(self, **attr)

        # Post-process
        if s3 is not None:
            postprocess = s3.get("postp")
        if postprocess is not None:
            output = postprocess(self, output)
        if output is not None and isinstance(output, dict):
            # Put a copy of r into the output for the view
            # to be able to make use of it
            output["r"] = self

        # Redirection
        # NB must re-read self.http/method here in case the have
        # been changed during prep, method handling or postp
        if self.next is not None and \
           (self.http != "GET" or self.method == "clear"):
            if isinstance(output, dict):
                form = output.get("form")
                if form:
                    if not hasattr(form, "errors"):
                        # Form embedded in a DIV together with other components
                        form = form.elements("form", first_only=True)
                        form = form[0] if form else None
                    if form and form.errors:
                        return output

            s3_keep_messages()
            redirect(self.next)

        return output

    # -------------------------------------------------------------------------
    def __GET(self, resource=None):
        """
            Get the GET method handler
        """

        method = self.method
        transform = False
        if method is None or method in ("read", "display", "update"):
            if self.transformable():
                method = "export_tree"
                transform = True
            elif self.component:
                resource = self.resource
                if self.interactive and resource.count() == 1:
                    # Load the record
                    if not resource._rows:
                        resource.load(start=0, limit=1)
                    if resource._rows:
                        self.record = resource._rows[0]
                        self.id = resource.get_id()
                        self.uid = resource.get_uid()
                if self.component.multiple and not self.component_id:
                    method = "list"
                else:
                    method = "read"
            elif self.id or method in ("read", "display", "update"):
                # Enforce single record
                resource = self.resource
                if not resource._rows:
                    resource.load(start=0, limit=1)
                if resource._rows:
                    self.record = resource._rows[0]
                    self.id = resource.get_id()
                    self.uid = resource.get_uid()
                else:
                    # Record not found => go to list
                    self.error(404, current.ERROR.BAD_RECORD,
                               next = self.url(id="", method=""),
                               )
                method = "read"
            else:
                method = "list"

        elif method in ("create", "update"):
            if self.transformable(method="import"):
                method = "import_tree"
                transform = True

        elif method == "delete":
            return self.__DELETE()

        elif method == "clear" and not self.component:
            s3_remove_last_record_id(self.tablename)
            self.next = URL(r=self, f=self.name)
            return lambda r, **attr: None

        elif self.transformable():
            transform = True

        return self.get_handler(method, transform=transform)

    # -------------------------------------------------------------------------
    def __PUT(self):
        """
            Get the PUT method handler
        """

        transform = self.transformable(method="import")

        method = self.method
        if not method and transform:
            method = "import_tree"

        return self.get_handler(method, transform=transform)

    # -------------------------------------------------------------------------
    def __POST(self):
        """
            Get the POST method handler
        """

        if self.method == "delete":
            return self.__DELETE()
        else:
            if self.transformable(method="import"):
                return self.__PUT()
            else:
                post_vars = self.post_vars
                table = self.target()[2]
                if "deleted" in table and "id" not in post_vars: # and "uuid" not in post_vars:
                    original = S3Resource.original(table, post_vars)
                    if original and original.deleted:
                        self.post_vars["id"] = original.id
                        self.vars["id"] = original.id
                return self.__GET()

    # -------------------------------------------------------------------------
    def __DELETE(self):
        """
            Get the DELETE method handler
        """

        if self.method:
            return self.get_handler(self.method)
        else:
            return self.get_handler("delete")

    # -------------------------------------------------------------------------
    # Built-in method handlers
    # -------------------------------------------------------------------------
    @staticmethod
    def get_tree(r, **attr):
        """
            XML Element tree export method

            @param r: the S3Request instance
            @param attr: controller attributes
        """

        get_vars = r.get_vars
        args = Storage()

        # Slicing
        start = get_vars.get("start")
        if start is not None:
            try:
                start = int(start)
            except ValueError:
                start = None
        limit = get_vars.get("limit")
        if limit is not None:
            try:
                limit = int(limit)
            except ValueError:
                limit = None

        # msince
        msince = get_vars.get("msince")
        if msince is not None:
            msince = s3_parse_datetime(msince)

        # Show IDs (default: False)
        if "show_ids" in get_vars:
            if get_vars["show_ids"].lower() == "true":
                current.xml.show_ids = True

        # Show URLs (default: True)
        if "show_urls" in get_vars:
            if get_vars["show_urls"].lower() == "false":
                current.xml.show_urls = False

        # Mobile data export (default: False)
        mdata = get_vars.get("mdata") == "1"

        # Maxbounds (default: False)
        maxbounds = False
        if "maxbounds" in get_vars:
            if get_vars["maxbounds"].lower() == "true":
                maxbounds = True
        if r.representation in ("gpx", "osm"):
            maxbounds = True

        # Components of the master resource (tablenames)
        if "mcomponents" in get_vars:
            mcomponents = get_vars["mcomponents"]
            if str(mcomponents).lower() == "none":
                mcomponents = None
            elif not isinstance(mcomponents, list):
                mcomponents = mcomponents.split(",")
        else:
            mcomponents = [] # all

        # Components of referenced resources (tablenames)
        if "rcomponents" in get_vars:
            rcomponents = get_vars["rcomponents"]
            if str(rcomponents).lower() == "none":
                rcomponents = None
            elif not isinstance(rcomponents, list):
                rcomponents = rcomponents.split(",")
        else:
            rcomponents = None

        # Maximum reference resolution depth
        if "maxdepth" in get_vars:
            try:
                args["maxdepth"] = int(get_vars["maxdepth"])
            except ValueError:
                pass

        # References to resolve (field names)
        if "references" in get_vars:
            references = get_vars["references"]
            if str(references).lower() == "none":
                references = []
            elif not isinstance(references, list):
                references = references.split(",")
        else:
            references = None # all

        # Export field selection
        if "fields" in get_vars:
            fields = get_vars["fields"]
            if str(fields).lower() == "none":
                fields = []
            elif not isinstance(fields, list):
                fields = fields.split(",")
        else:
            fields = None # all

        # Find XSLT stylesheet
        stylesheet = r.stylesheet()

        # Add stylesheet parameters
        if stylesheet is not None:
            if r.component:
                args["id"] = r.id
                args["component"] = r.component.tablename
                if r.component.alias:
                    args["alias"] = r.component.alias
            mode = get_vars.get("xsltmode")
            if mode is not None:
                args["mode"] = mode

        # Set response headers
        response = current.response
        s3 = response.s3
        headers = response.headers
        representation = r.representation
        if representation in s3.json_formats:
            as_json = True
            default = "application/json"
        else:
            as_json = False
            default = "text/xml"
        headers["Content-Type"] = s3.content_type.get(representation,
                                                      default)

        # Export the resource
        resource = r.resource
        target = r.target()[3]
        if target == resource.tablename:
            # Master resource targetted
            target = None
        output = resource.export_xml(start = start,
                                     limit = limit,
                                     msince = msince,
                                     fields = fields,
                                     dereference = True,
                                     # maxdepth in args
                                     references = references,
                                     mdata = mdata,
                                     mcomponents = mcomponents,
                                     rcomponents = rcomponents,
                                     stylesheet = stylesheet,
                                     as_json = as_json,
                                     maxbounds = maxbounds,
                                     target = target,
                                     **args)
        # Transformation error?
        if not output:
            r.error(400, "XSLT Transformation Error: %s " % current.xml.error)

        return output

    # -------------------------------------------------------------------------
    @staticmethod
    def put_tree(r, **attr):
        """
            XML Element tree import method

            @param r: the S3Request method
            @param attr: controller attributes
        """

        get_vars = r.get_vars

        # Skip invalid records?
        if "ignore_errors" in get_vars:
            ignore_errors = True
        else:
            ignore_errors = False

        # Find all source names in the URL vars
        def findnames(get_vars, name):
            nlist = []
            if name in get_vars:
                names = get_vars[name]
                if isinstance(names, (list, tuple)):
                    names = ",".join(names)
                names = names.split(",")
                for n in names:
                    if n[0] == "(" and ")" in n[1:]:
                        nlist.append(n[1:].split(")", 1))
                    else:
                        nlist.append([None, n])
            return nlist
        filenames = findnames(get_vars, "filename")
        fetchurls = findnames(get_vars, "fetchurl")
        source_url = None

        # Get the source(s)
        s3 = current.response.s3
        json_formats = s3.json_formats
        csv_formats = s3.csv_formats
        source = []
        representation = r.representation
        if representation in json_formats or representation in csv_formats:
            if filenames:
                try:
                    for f in filenames:
                        source.append((f[0], open(f[1], "rb")))
                except:
                    source = []
            elif fetchurls:
                try:
                    for u in fetchurls:
                        source.append((u[0], urlopen(u[1])))
                except:
                    source = []
            elif r.http != "GET":
                source = r.read_body()
        else:
            if filenames:
                source = filenames
            elif fetchurls:
                source = fetchurls
                # Assume only 1 URL for GeoRSS feed caching
                source_url = fetchurls[0][1]
            elif r.http != "GET":
                source = r.read_body()
        if not source:
            if filenames or fetchurls:
                # Error: source not found
                r.error(400, "Invalid source")
            else:
                # No source specified => return resource structure
                return r.get_struct(r, **attr)

        # Find XSLT stylesheet
        stylesheet = r.stylesheet(method="import")
        # Target IDs
        if r.method == "create":
            _id = None
        else:
            _id = r.id

        # Transformation mode?
        if "xsltmode" in get_vars:
            args = {"xsltmode": get_vars["xsltmode"]}
        else:
            args = {}
        # These 3 options are called by gis.show_map() & read by the
        # GeoRSS Import stylesheet to populate the gis_cache table
        # Source URL: For GeoRSS/KML Feed caching
        if source_url:
            args["source_url"] = source_url
        # Data Field: For GeoRSS/KML Feed popups
        if "data_field" in get_vars:
            args["data_field"] = get_vars["data_field"]
        # Image Field: For GeoRSS/KML Feed popups
        if "image_field" in get_vars:
            args["image_field"] = get_vars["image_field"]

        # Format type?
        if representation in json_formats:
            representation = "json"
        elif representation in csv_formats:
            representation = "csv"
        else:
            representation = "xml"

        try:
            output = r.resource.import_xml(source,
                                           id=_id,
                                           format=representation,
                                           files=r.files,
                                           stylesheet=stylesheet,
                                           ignore_errors=ignore_errors,
                                           **args)
        except IOError:
            current.auth.permission.fail()
        except SyntaxError:
            e = sys.exc_info()[1]
            if hasattr(e, "message"):
                e = e.message
            r.error(400, e)

        return output

    # -------------------------------------------------------------------------
    @staticmethod
    def get_struct(r, **attr):
        """
            Resource structure introspection method

            @param r: the S3Request instance
            @param attr: controller attributes
        """

        response = current.response
        json_formats = response.s3.json_formats
        if r.representation in json_formats:
            as_json = True
            content_type = "application/json"
        else:
            as_json = False
            content_type = "text/xml"
        get_vars = r.get_vars
        meta = str(get_vars.get("meta", False)).lower() == "true"
        opts = str(get_vars.get("options", False)).lower() == "true"
        refs = str(get_vars.get("references", False)).lower() == "true"
        stylesheet = r.stylesheet()
        output = r.resource.export_struct(meta=meta,
                                          options=opts,
                                          references=refs,
                                          stylesheet=stylesheet,
                                          as_json=as_json)
        if output is None:
            # Transformation error
            r.error(400, current.xml.error)
        response.headers["Content-Type"] = content_type
        return output

    # -------------------------------------------------------------------------
    @staticmethod
    def get_fields(r, **attr):
        """
            Resource structure introspection method (single table)

            @param r: the S3Request instance
            @param attr: controller attributes
        """

        representation = r.representation
        if representation == "xml":
            output = r.resource.export_fields(component=r.component_name)
            content_type = "text/xml"
        elif representation == "s3json":
            output = r.resource.export_fields(component=r.component_name,
                                              as_json=True)
            content_type = "application/json"
        else:
            r.error(415, current.ERROR.BAD_FORMAT)
        response = current.response
        response.headers["Content-Type"] = content_type
        return output

    # -------------------------------------------------------------------------
    @staticmethod
    def get_options(r, **attr):
        """
            Field options introspection method (single table)

            @param r: the S3Request instance
            @param attr: controller attributes
        """

        get_vars = r.get_vars

        items = get_vars.get("field")
        if items:
            if not isinstance(items, (list, tuple)):
                items = [items]
            fields = []
            add_fields = fields.extend
            for item in items:
                f = item.split(",")
                if f:
                    add_fields(f)
        else:
            fields = None

        if "hierarchy" in get_vars:
            hierarchy = get_vars["hierarchy"].lower() not in ("false", "0")
        else:
            hierarchy = False

        if "only_last" in get_vars:
            only_last = get_vars["only_last"].lower() not in ("false", "0")
        else:
            only_last = False

        if "show_uids" in get_vars:
            show_uids = get_vars["show_uids"].lower() not in ("false", "0")
        else:
            show_uids = False

        representation = r.representation
        flat = False
        if representation == "xml":
            only_last = False
            as_json = False
            content_type = "text/xml"
        elif representation == "s3json":
            show_uids = False
            as_json = True
            content_type = "application/json"
        elif representation == "json" and fields and len(fields) == 1:
            # JSON option supported for flat data structures only
            # e.g. for use by jquery.jeditable
            flat = True
            show_uids = False
            as_json = True
            content_type = "application/json"
        else:
            r.error(415, current.ERROR.BAD_FORMAT)

        component = r.component_name
        output = r.resource.export_options(component=component,
                                           fields=fields,
                                           show_uids=show_uids,
                                           only_last=only_last,
                                           hierarchy=hierarchy,
                                           as_json=as_json,
                                           )

        if flat:
            s3json = json.loads(output)
            output = {}
            options = s3json.get("option")
            if options:
                for item in options:
                    output[item.get("@value")] = item.get("$", "")
            output = json.dumps(output)

        current.response.headers["Content-Type"] = content_type
        return output

    # -------------------------------------------------------------------------
    # Tools
    # -------------------------------------------------------------------------
    def factory(self, **args):
        """
            Generate a new request for the same resource

            @param args: arguments for request constructor
        """

        return s3_request(r=self, **args)

    # -------------------------------------------------------------------------
    def __getattr__(self, key):
        """
            Called upon S3Request.<key> - looks up the value for the <key>
            attribute. Falls back to current.request if the attribute is
            not defined in this S3Request.

            @param key: the key to lookup
        """

        if key in self.__dict__:
            return self.__dict__[key]

        sentinel = object()
        value = getattr(current.request, key, sentinel)
        if value is sentinel:
            raise AttributeError
        return value

    # -------------------------------------------------------------------------
    def transformable(self, method=None):
        """
            Check the request for a transformable format

            @param method: "import" for import methods, else None
        """

        if self.representation in ("html", "aadata", "popup", "iframe"):
            return False

        stylesheet = self.stylesheet(method=method, skip_error=True)

        if not stylesheet and self.representation != "xml":
            return False
        else:
            return True

    # -------------------------------------------------------------------------
    def actuate_link(self, component_id=None):
        """
            Determine whether to actuate a link or not

            @param component_id: the component_id (if not self.component_id)
        """

        if not component_id:
            component_id = self.component_id
        if self.component:
            single = component_id != None
            component = self.component
            if component.link:
                actuate = self.component.actuate
                if "linked" in self.get_vars:
                    linked = self.get_vars.get("linked", False)
                    linked = linked in ("true", "True")
                    if linked:
                        actuate = "replace"
                    else:
                        actuate = "hide"
                if actuate == "link":
                    if self.method != "delete" and self.http != "DELETE":
                        return single
                    else:
                        return not single
                elif actuate == "replace":
                    return True
                #elif actuate == "embed":
                    #raise NotImplementedError
                else:
                    return False
            else:
                return True
        else:
            return False

    # -------------------------------------------------------------------------
    @staticmethod
    def unauthorised():
        """
            Action upon unauthorised request
        """

        current.auth.permission.fail()

    # -------------------------------------------------------------------------
    def error(self, status, message, tree=None, next=None):
        """
            Action upon error

            @param status: HTTP status code
            @param message: the error message
            @param tree: the tree causing the error
        """

        if self.representation == "html":
            current.session.error = message
            if next is not None:
                redirect(next)
            else:
                redirect(URL(r=self, f="index"))
        else:
            headers = {"Content-Type":"application/json"}
            current.log.error(message)
            raise HTTP(status,
                       body = current.xml.json_message(success = False,
                                                       statuscode = status,
                                                       message = message,
                                                       tree = tree),
                       web2py_error = message,
                       **headers)

    # -------------------------------------------------------------------------
    def url(self,
            id=None,
            component=None,
            component_id=None,
            target=None,
            method=None,
            representation=None,
            vars=None,
            host=None):
        """
            Returns the URL of this request, use parameters to override
            current requests attributes:

                - None to keep current attribute (default)
                - 0 or "" to set attribute to NONE
                - value to use explicit value

            @param id: the master record ID
            @param component: the component name
            @param component_id: the component ID
            @param target: the target record ID (choose automatically)
            @param method: the URL method
            @param representation: the representation for the URL
            @param vars: the URL query variables
            @param host: string to force absolute URL with host (True means http_host)

            Particular behavior:
                - changing the master record ID resets the component ID
                - removing the target record ID sets the method to None
                - removing the method sets the target record ID to None
                - [] as id will be replaced by the "[id]" wildcard
        """

        if vars is None:
            vars = self.get_vars
        elif vars and isinstance(vars, str):
            # We've come from a dataTable_vars which has the vars as
            # a JSON string, but with the wrong quotation marks
            vars = json.loads(vars.replace("'", "\""))

        if "format" in vars:
            del vars["format"]

        args = []

        cname = self.component_name

        # target
        if target is not None:
            if cname and (component is None or component == cname):
                component_id = target
            else:
                id = target

        # method
        default_method = False
        if method is None:
            default_method = True
            method = self.method
        elif method == "":
            # Switch to list? (= method="" and no explicit target ID)
            if component_id is None:
                if self.component_id is not None:
                    component_id = 0
                elif not self.component:
                    if id is None:
                        if self.id is not None:
                            id = 0
            method = None

        # id
        if id is None:
            id = self.id
        elif id in (0, ""):
            id = None
        elif id in ([], "[id]", "*"):
            id = "[id]"
            component_id = 0
        elif str(id) != str(self.id):
            component_id = 0

        # component
        if component is None:
            component = cname
        elif component == "":
            component = None
        if cname and cname != component or not component:
            component_id = 0

        # component_id
        if component_id is None:
            component_id = self.component_id
        elif component_id == 0:
            component_id = None
            if self.component_id and default_method:
                method = None

        if id is None and self.id and \
           (not component or not component_id) and default_method:
            method = None

        if id:
            args.append(id)
        if component:
            args.append(component)
        if component_id:
            args.append(component_id)
        if method:
            args.append(method)

        # representation
        if representation is None:
            representation = self.representation
        elif representation == "":
            representation = self.DEFAULT_REPRESENTATION
        f = self.function
        if not representation == self.DEFAULT_REPRESENTATION:
            if len(args) > 0:
                args[-1] = "%s.%s" % (args[-1], representation)
            else:
                f = "%s.%s" % (f, representation)

        return URL(r=self,
                   c=self.controller,
                   f=f,
                   args=args,
                   vars=vars,
                   host=host)

    # -------------------------------------------------------------------------
    def target(self):
        """
            Get the target table of the current request

            @return: a tuple of (prefix, name, table, tablename) of the target
                resource of this request

            @todo: update for link table support
        """

        component = self.component
        if component is not None:
            link = self.component.link
            if link and not self.actuate_link():
                return(link.prefix,
                       link.name,
                       link.table,
                       link.tablename)
            return (component.prefix,
                    component.name,
                    component.table,
                    component.tablename)
        else:
            return (self.prefix,
                    self.name,
                    self.table,
                    self.tablename)

    # -------------------------------------------------------------------------
    @property
    def viewing(self):
        """
            Parse the "viewing" URL parameter, frequently used for
            perspective discrimination and processing in prep

            @returns: tuple (tablename, record_id) if "viewing" is set,
                      None otherwise
        """

        get_vars = self.get_vars
        if "viewing" in get_vars:
            try:
                tablename, record_id = get_vars.get("viewing").split(".")
            except (AttributeError, ValueError):
                return None
            try:
                record_id = int(record_id)
            except (TypeError, ValueError):
                return None
            return tablename, record_id

        return None

    # -------------------------------------------------------------------------
    def stylesheet(self, method=None, skip_error=False):
        """
            Find the XSLT stylesheet for this request

            @param method: "import" for data imports, else None
            @param skip_error: do not raise an HTTP error status
                               if the stylesheet cannot be found
        """

        representation = self.representation

        # Native S3XML?
        if representation == "xml":
            return None

        # External stylesheet specified?
        if "transform" in self.vars:
            return self.vars["transform"]

        component = self.component
        resourcename = component.name if component else self.name

        # Stylesheet attached to the request?
        extension = self.XSLT_EXTENSION
        filename = "%s.%s" % (resourcename, extension)
        if filename in self.post_vars:
            p = self.post_vars[filename]
            import cgi
            if isinstance(p, cgi.FieldStorage) and p.filename:
                return p.file

        # Look for stylesheet in file system
        folder = self.folder
        if method != "import":
            method = "export"
        stylesheet = None

        # Custom transformation stylesheet in template?
        if not stylesheet:
            formats = current.deployment_settings.get_xml_formats()
            if isinstance(formats, dict) and representation in formats:
                stylesheets = formats[representation]
                if isinstance(stylesheets, str) and stylesheets:
                    stylesheets = stylesheets.split("/") + ["formats"]
                    path = os.path.join("modules", "templates", *stylesheets)
                    filename = "%s.%s" % (method, extension)
                    stylesheet = os.path.join(folder, path, representation, filename)

        # Transformation stylesheet at standard location?
        if not stylesheet:
            path = self.XSLT_PATH
            filename = "%s.%s" % (method, extension)
            stylesheet = os.path.join(folder, path, representation, filename)

        if not os.path.exists(stylesheet):
            if not skip_error:
                self.error(501, "%s: %s" % (current.ERROR.BAD_TEMPLATE,
                                            stylesheet,
                                            ))
            stylesheet = None

        return stylesheet

    # -------------------------------------------------------------------------
    def read_body(self):
        """
            Read data from request body
        """

        self.files = Storage()
        content_type = self.env.get("content_type")

        source = []
        if content_type and content_type.startswith("multipart/"):
            import cgi
            ext = ".%s" % self.representation
            post_vars = self.post_vars
            for v in post_vars:
                p = post_vars[v]
                if isinstance(p, cgi.FieldStorage) and p.filename:
                    self.files[p.filename] = p.file
                    if p.filename.endswith(ext):
                        source.append((v, p.file))
                elif v.endswith(ext):
                    if isinstance(p, cgi.FieldStorage):
                        source.append((v, p.value))
                    elif isinstance(p, str):
                        source.append((v, StringIO(p)))
        else:
            s = self.body
            s.seek(0)
            source.append(s)

        return source

    # -------------------------------------------------------------------------
    def customise_resource(self, tablename=None):
        """
            Invoke the customization callback for a resource.

            @param tablename: the tablename of the resource; if called
                              without tablename it will invoke the callbacks
                              for the target resources of this request:
                                - master
                                - active component
                                - active link table
                              (in this order)

            Resource customization functions can be defined like:

                def customise_resource_my_table(r, tablename):

                    current.s3db.configure(tablename,
                                           my_custom_setting = "example")
                    return

                settings.customise_resource_my_table = \
                                        customise_resource_my_table

            @note: the hook itself can call r.customise_resource in order
                   to cascade customizations as necessary
            @note: if a table is customised that is not currently loaded,
                   then it will be loaded for this process
        """

        if tablename is None:
            customise = self.customise_resource

            customise(self.resource.tablename)
            component = self.component
            if component:
                customise(component.tablename)
            link = self.link
            if link:
                customise(link.tablename)
        else:
            # Always load the model first (otherwise it would
            # override the custom settings when loaded later)
            db = current.db
            if tablename not in db:
                current.s3db.table(tablename)
            customise = current.deployment_settings.customise_resource(tablename)
            if customise:
                customise(self, tablename)

# =============================================================================
# Global functions
#
def s3_request(*args, **kwargs):
    """
        Helper function to generate S3Request instances

        @param args: arguments for the S3Request
        @param kwargs: keyword arguments for the S3Request

        @keyword catch_errors: if set to False, errors will be raised
                               instead of returned to the client, useful
                               for optional sub-requests, or if the caller
                               implements fallbacks
    """

    catch_errors = kwargs.pop("catch_errors", True)

    error = None
    try:
        r = S3Request(*args, **kwargs)
    except (AttributeError, SyntaxError):
        if catch_errors is False:
            raise
        error = 400
    except KeyError:
        if catch_errors is False:
            raise
        error = 404
    if error:
        message = sys.exc_info()[1]
        if hasattr(message, "message"):
            message = message.message
        if current.auth.permission.format == "html":
            current.session.error = message
            redirect(URL(f="index"))
        else:
            headers = {"Content-Type":"application/json"}
            current.log.error(message)
            raise HTTP(error,
                       body=current.xml.json_message(success=False,
                                                     statuscode=error,
                                                     message=message,
                                                     ),
                       web2py_error=message,
                       **headers)
    return r

# END =========================================================================
