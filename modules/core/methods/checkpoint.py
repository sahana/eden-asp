"""
    Checkpoint UI (DVR)

    Copyright: 2023-2023 (c) Sahana Software Foundation

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

__all__ = ("Checkpoint",
           )

import datetime
import json

from gluon import current, URL, \
                  A, DIV, H4, INPUT, SPAN, \
                  SQLFORM, IS_LENGTH, IS_NOT_EMPTY

from s3dal import Field

from .base import CRUDMethod
from .presence import SitePresence
from ..tools import s3_encode_iso_datetime, s3_fullname, s3_str, S3DateTime, FormKey
from ..resource import FS
from ..ui import S3QRInput, ICON

# =============================================================================
class Checkpoint(CRUDMethod):
    """ User interface for checkpoints to register DVR case events """

    # Action to look up flag instructions for
    # TODO modify to show instructions per event class
    # TODO extend with option to restrict event types per flag
    ACTION = "id-check"

    # Event classes this method is intended for
    EVENT_CLASSES = ("A", "C") # = Administrative + Checkpoint

    # -------------------------------------------------------------------------
    def apply_method(self, r, **attr):
        """
            Entry point for CRUD controller

            Args:
                r: the CRUDRequest instance
                attr: controller parameters
        """

        # TODO no permission check here?
        #if not self.permitted():
        #    current.auth.permission.fail()

        output = {}

        representation = r.representation
        http = r.http

        if representation == "html":

            if http == "GET":
                output = self.registration_form(r, **attr)
            else:
                r.error(405, current.ERROR.BAD_METHOD)

        elif representation == "json":

            if http == "GET":
                output = self.event_types(r, **attr)
            elif http == "POST":
                output = self.check_or_register(r, **attr)
            else:
                r.error(405, current.ERROR.BAD_METHOD)

        else:
            r.error(415, current.ERROR.BAD_FORMAT)

        return output

    # -------------------------------------------------------------------------
    def registration_form(self, r, **attr):
        # TODO docstring
        # TODO refactor

        # TODO Permissions check
        # Must be permitted to create case events

        T = current.T
        #s3db = current.s3db

        response = current.response
        settings = current.deployment_settings

        output = {}
        widget_id = "case-event-form"

        # Add organisation selector
        organisations = self.get_organisations() # {id: Row(id, name), _default: id}
        selector = self.organisation_selector(organisations, widget_id=widget_id)
        output.update(selector)

        # Default organisation_id
        organisation_id = organisations.get("_default") # Could be None

        # Add event type selector
        event_types = self.get_event_types(organisation_id) # {id: Row(id, code, name), _default: id}, or None
        selector = self.event_type_selector(event_types, widget_id=widget_id)
        output.update(selector)

        # Default event type
        default = event_types.get("_default")
        if default:
            event_type = event_types.get(default)
            event_code = event_type.code if event_type else None
        else:
            event_type = None
            event_code = None

        label_input = self.label_input
        use_qr_code = settings.get_org_site_presence_qrcode()
        if use_qr_code:
            if use_qr_code is True:
                label_input = S3QRInput()
            elif isinstance(use_qr_code, tuple):
                pattern, index = use_qr_code[:2]
                label_input = S3QRInput(pattern=pattern, index=index)

        # Standard form fields and data
        formfields = [Field("label",
                            label = T("ID"),
                            requires = [IS_NOT_EMPTY(error_message=T("Enter or scan an ID")),
                                        IS_LENGTH(512, minsize=1),
                                        ],
                            widget = label_input,
                            ),
                      Field("person",
                            label = "",
                            writable = False,
                            default = "",
                            ),
                      Field("flaginfo",
                            label = "",
                            writable = False,
                            ),
                      Field("details",
                            label = "",
                            writable = False,
                            ),
                      Field("family",
                            label = T("Family"),
                            writable = False,
                            ),
                      ]

        data = {"id": "",
                "label": "",
                "person": "",
                "flaginfo": "",
                "family": "",
                "details": "",
                }

        # Hidden fields to store event type, scanner, flag info and permission
        hidden = {"event": event_code,
                  "actionable": None,
                  "permitted": None,
                  "flags": [],
                  "familyinfo": None,
                  "intervals": None,
                  "image": None,
                  "_formkey": FormKey("case-event-registration").generate(),
                  }

        # TODO Additional form data
        #widget_id, submit = self.get_form_data(person,
                                               #formfields,
                                               #data,
                                               #hidden,
                                               #permitted = permitted,
                                               #)

        # Form buttons
        check_btn = INPUT(_class = "small secondary button check-btn",
                          _name = "check",
                          _type = "submit",
                          _value = T("Check ID"),
                          )
        submit_btn = INPUT(_class = "small primary button submit-btn hide",
                           _disabled = "disabled",
                           _name = "submit",
                           _type = "submit",
                           _value = T("Register"),
                           )
        buttons = [check_btn, submit_btn]

        # Add the cancel-action
        buttons.append(A(T("Cancel"), _class = "cancel-action cancel-form-btn action-lnk"))

        resourcename = r.resource.name

        # Generate the form and add it to the output
        formstyle = settings.get_ui_formstyle()
        form = SQLFORM.factory(record = data,
                               showid = False,
                               formstyle = formstyle,
                               table_name = resourcename,
                               buttons = buttons,
                               hidden = hidden,
                               _id = widget_id,
                               _class = "case-event-registration",
                               *formfields)
        output["form"] = form
        output["picture"] = DIV(_class = "panel profile-picture",
                                _id = "%s-picture" % widget_id,
                                )

        # Custom view
        response.view = self._view(r, "dvr/register_case_event.html")

        # Show profile picture by default or only on demand?
        show_picture = settings.get_dvr_event_registration_show_picture()

        # Inject JS
        options = {"tablename": resourcename,
                   "ajaxURL": r.url(None,
                                    method = "register",
                                    representation = "json",
                                    ),
                   "showPicture": show_picture,
                   "showPictureText": s3_str(T("Show Picture")),
                   "hidePictureText": s3_str(T("Hide Picture")),
                   "selectAllText": s3_str(T("Select All")),
                   "noEventsLabel": s3_str(T("No event types available")),
                   "selectEventLabel": s3_str(T("Please select an event type")),
                   }
        self.inject_js(widget_id, options)

        return output

    # -------------------------------------------------------------------------
    @classmethod
    def event_types(cls, r, **attr):
        """
            Returns the event types for the organisation specified by the
            URL query parameter "org" (=the organisation ID)

            Args:
                r: the CRUDRequest instance
                attr: controller parameters

            Returns:
                - a JSON object {"types": [[code, name], ...],
                                 "default": [code, name],
                                 }
        """

        T = current.T

        organisation_id = r.get_vars.get("org")
        if organisation_id:
            # Get the event types for the organisation
            event_types = cls.get_event_types(organisation_id)

            # Build the type list
            types, default = [], None
            for k, v in event_types.items():
                t = event_types[v] if k == "_default" else v
                types.append([t.code, s3_str(T(t.name)), t.register_multiple])

            # Sort types alphabetically by label
            output = {"types": sorted(types, key=lambda i: i[1]),
                      "default": default,
                      }
        else:
            output = {"types": [], "default": None}


        current.response.headers["Content-Type"] = "application/json"
        return json.dumps(output)

    # -------------------------------------------------------------------------
    def check_or_register(self, r, **attr):
        # TODO docstring

        # JSON format:
        #    {"a": the action ("check"|"register")
        #     "l": the PE label(s)
        #     "o": the organisation ID
        #     "e": the event type code
        #     "k": XSRF token
        #     }

        # Load JSON data from request body
        s = r.body
        s.seek(0)
        try:
            json_data = json.load(s)
        except (ValueError, TypeError):
            r.error(400, current.ERROR.BAD_REQUEST)

        # XSRF protection
        formkey = FormKey("case-event-registration")
        if not formkey.verify(json_data, variable="k", invalidate=False):
            r.unauthorised()

        # Dispatch by action
        action = json_data.get("a")
        if action == "check":
            output = self.check(r, json_data)
        elif action == "register":
            output = self.register(r, json_data)
        else:
            r.error(400, current.ERROR.BAD_REQUEST)

        # output format:
        # {l: the actual PE label (to update the input field),
        #  p: the person details,
        #  d: the family details,
        #  f: [{n: the flag name
        #      i: the flag instructions
        #      },
        #      ...],
        #  b: profile picture URL,
        #  i: {<event_code>: [<msg>, <blocked_until_datetime>]},
        #  s: whether the action is permitted or not
        #  e: form error (for label field)
        #  a: error message
        #  w: warning message
        #  m: success message
        #  }


        current.response.headers["Content-Type"] = "application/json"
        return json.dumps(output)

    # -------------------------------------------------------------------------
    def check(self, r, json_data):
        # TODO docstring
        # JSON format:
        #    {"a": the action ("check"|"register")
        #     "l": the PE label(s)
        #     "o": the organisation ID
        #     "e": the event type code
        #     "k": XSRF token
        #     }

        # TODO permissions check
        # Must be permitted to read person data for the selected org
        # => get permitted realms
        # => if realms is not None and org in realms => okay
        # => if realms is None => okay
        # => otherwise: forbidden

        # The organisation
        # TODO does this need to be validated?
        organisation_id = json_data.get("o")

        # Identify the person
        label = json_data.get("l")
        validate = current.deployment_settings.get_org_site_presence_validate_id()
        if callable(validate):
            label, advice, error = validate(label)
            person = self.get_person(label, organisation_id) if label else None
            if error:
                # TODO show as advice
                # TODO disable action
                pass
        else:
            label, advice, error = label, None, None
            person = self.get_person(label, organisation_id)
        if not person:
            if not error:
                advice = current.T("No person found with this ID number")
            else:
                advice = None

        output = {"l": label,
                  "a": s3_str(advice) if advice else None,
                  "e": s3_str(error) if error else None,
                  }

        if person:
            output["l"] = person.pe_label
            output["p"] = self.person_details(person).xml().decode('utf-8')
            output["f"] = self.flags(person, organisation_id=organisation_id)
            output["b"] = self.profile_picture(person)

            # Family members
            family = self.get_family_members(person, organisation_id)
            if family:
                output["x"] = family

            # Blocked events
            blocked = self.get_blocked_events(person.id,
                                              organisation_id,
                                              serializable = True,
                                              )
            if blocked:
                # TODO format as r: {event_code: {m: message, e: earliest}}
                output["i"] = blocked

            output["s"] = True
        else:
            output["p"] = None
            output["s"] = False

        # output format:
        # {l: the actual PE label (to update the input field),
        #  p: the person details,                                # TODO Break up as n(ame) d(ate_of_birth)
        #  f: [{n: the flag name
        #      i: the flag instructions
        #      },
        #      ...],
        #  x: the family details,
        #  d: action details,                                    # TODO proper explanation
        #  b: profile picture URL,                               # TODO Change into i(mage)
        #  i: {<event_code>: [<msg>, <blocked_until_datetime>]}, # TODO Change into "r" (=rules)
        #  u: actionable info (e.g. which payment to pay out)
        #  s: whether the action is permitted or not             # TODO what for?

        #  e: form error (for label field)
        #  a: error message
        #  w: warning message
        #  m: success message
        #  }

        return output

    # -------------------------------------------------------------------------
    def register(self, r, json_data):
        # TODO docstring
        # JSON format:
        #    {"a": the action ("check"|"register")
        #     "l": the PE label(s)
        #     "o": the organisation ID
        #     "e": the event type code
        #     "k": XSRF token
        #     }

        T = current.T

        # TODO permissions check
        # Must be permitted to create case events for the selected org

        organisation_id = json_data.get("o")

        # Identify the event type
        code = json_data.get("e")
        event_type = self.get_event_type(code, organisation_id)
        persons = []

        if not event_type:
            # TODO Error: invalid event type
            pass

        else:
            # Identify the person(s)
            # TODO check if event type permits group registration
            labels = json_data.get("l")
            if not labels:
                labels = []
            elif not isinstance(labels, list):
                labels = [labels]
            else:
                # TODO multiple only permitted if event_type has register_multiple
                pass

            validate = current.deployment_settings.get_org_site_presence_validate_id()
            for i, label in enumerate(labels):
                if i > 1:
                    validate = None
                if callable(validate):
                    label, advice, error = validate(label)
                    person = self.get_person(label, organisation_id) if label else None
                    #if advice:
                        #output["q"] = s3_str(advice)
                else:
                    label, advice, error = label, None, None
                    person = self.get_person(label, organisation_id)
                if person:
                    persons.append(person)
                else:
                    # TODO Error: person not found
                    break

        # TODO register_bare
        if persons and event_type:
            for person in persons:
                # TODO Check event type not blocked for that person
                error = self.register_bare(person, event_type.id)
                if error:
                    # TODO Error: event registration failed
                    break
        elif not persons:
            error = T("Person not found")
        else:
            error = T("Invalid event type")

        # output format:
        # {l: the actual PE label (to update the input field),
        #  p: the person details,
        #  d: the family details,
        #  f: [{n: the flag name
        #      i: the flag instructions
        #      },
        #      ...],
        #  b: profile picture URL,
        #  i: {<event_code>: [<msg>, <blocked_until_datetime>]},
        #  s: whether the action is permitted or not
        #  e: form error (for label field)
        #  a: error message
        #  w: warning message
        #  m: success message
        #  }
        if error:
            output = {"a": s3_str(error)}
        else:
            output = {"m": s3_str(T("Event registered successfully"))}

        return output

    # -------------------------------------------------------------------------
    def register_bare(self, person, event_type_id):
        # TODO docstring

        #print("Register bare:", person, event_type_id)

        s3db = current.s3db

        #ctable = s3db.dvr_case
        etable = s3db.dvr_case_event

        # TODO Fix or remove this
        #      - will require the case org to match the event type
        ## Get the case ID for the person_id
        #query = (ctable.person_id == person_id) & \
                #(ctable.deleted != True)
        #case = current.db(query).select(ctable.id,
                                        #limitby=(0, 1),
                                        #).first()
        #if case:
            #case_id = case.id
        #else:
            #case_id = None

        # Customise event resource
        from ..controller import CRUDRequest
        r = CRUDRequest("dvr", "case_event", current.request, args=[], get_vars={})
        r.customise_resource("dvr_case_event")

        data = {"person_id": person.id,
                #"case_id": case_id,
                "type_id": event_type_id,
                "date": current.request.utcnow,
                }
        record_id = etable.insert(**data)
        if record_id:
            # Set record owner
            auth = current.auth
            auth.s3_set_record_owner(etable, record_id)
            auth.s3_make_session_owner(etable, record_id)
            # Execute onaccept
            data["id"] = record_id
            s3db.onaccept(etable, data, method="create")

        # TODO should just return True or False
        return None if record_id else "Registration failed"

    # -------------------------------------------------------------------------
    @staticmethod
    def get_person(label, organisation_id=None):
        """
            Get the person record for the label

            Args:
                label: the PE label
                organisation_id: the organisation ID
        """

        if not label or not organisation_id:
            return None

        # Fields to extract
        fields = ["id",
                  "pe_id",
                  "pe_label",
                  "first_name",
                  "middle_name",
                  "last_name",
                  "date_of_birth",
                  "gender",
                  ]

        query = (FS("pe_label").upper() == label.upper()) & \
                (FS("dvr_case.organisation_id") == organisation_id) & \
                (FS("dvr_case.status_id$is_closed") == False)

        presource = current.s3db.resource("pr_person",
                                          components = [],
                                          filter = query,
                                          )
        rows = presource.select(fields, limit=1, as_rows=True)

        return rows[0] if rows else None

    # -------------------------------------------------------------------------
    @staticmethod
    def person_details(person):
        """
            Format the person details

            Args:
                person: the person record (Row)
        """

        T = current.T

        name = s3_fullname(person)
        dob = person.date_of_birth
        if dob:
            dob = S3DateTime.date_represent(dob)
            details = "%s (%s %s)" % (name, T("Date of Birth"), dob)
        else:
            details = name

        output = SPAN(details, _class = "person-details")
        return output

    # -------------------------------------------------------------------------
    @staticmethod
    def profile_picture(person):
        """
            Get the profile picture URL for a person

            Args:
                person: the person record (Row)

            Returns:
                the profile picture URL (relative URL), or None if
                no profile picture is available for that person
        """

        try:
            pe_id = person.pe_id
        except AttributeError:
            return None

        table = current.s3db.pr_image
        query = (table.pe_id == pe_id) & \
                (table.profile == True) & \
                (table.deleted != True)
        row = current.db(query).select(table.image, limitby=(0, 1)).first()

        return URL(c="default", f="download", args=row.image) if row else None

    # -------------------------------------------------------------------------
    @staticmethod
    def flags(person, organisation_id=None):
        # TODO take organisation_id into account
        # TODO docstring

        T = current.T

        flags = []

        flag_info = current.s3db.dvr_get_flag_instructions(person.id,
                                                           organisation_id = organisation_id,
                                                           )
        info = flag_info["info"]

        for flagname, instructions in info:
            flags.append({"n": s3_str(T(flagname)),
                          "i": s3_str(T(instructions)),
                          })
        return flags

    # -------------------------------------------------------------------------
    # Utilities
    # -------------------------------------------------------------------------
    # TODO order methods
    # -------------------------------------------------------------------------
    @staticmethod
    def get_form_data(person, formfields, data, hidden, permitted=False):
        # TODO deprecate?
        """
            Helper function to extend the form

            Args:
                person: the person (Row)
                formfields: list of form fields (Field)
                data: the form data (dict)
                hidden: hidden form fields (dict)
                permitted: whether the action is permitted

            Returns:
                tuple (widget_id, submit_label)
        """

        T = current.T
        s3db = current.s3db

        # Extend form with household size info
        if person:
            details = s3db.dvr_get_household_size(person.id,
                                                  dob = person.date_of_birth,
                                                  )
        else:
            details = ""
        formfields.extend([Field("details",
                                 label = T("Family"),
                                 writable = False,
                                 ),
                           ])
        data["details"] = details

        widget_id = "case-event-form"
        submit = current.T("Register")

        return widget_id, submit

    # -------------------------------------------------------------------------
    @staticmethod
    def label_input(field, value, **attributes):
        """
            Custom widget for label input, providing a clear-button
            (for ease of use on mobile devices where no ESC exists)

            Args:
                field: the Field
                value: the current value
                attributes: HTML attributes

            Note:
                expects Foundation theme
        """

        from gluon.sqlhtml import StringWidget

        default = {"value": (value is not None and str(value)) or ""}
        attr = StringWidget._attributes(field, default, **attributes)

        placeholder = current.T("Enter or scan ID")
        attr["_placeholder"] = placeholder

        postfix = ICON("fa fa-close")

        widget = DIV(DIV(INPUT(**attr),
                         _class="small-11 columns",
                         ),
                     DIV(SPAN(postfix, _class="postfix clear-btn"),
                         _class="small-1 columns",
                         ),
                     _class="row collapse",
                     )

        return widget

    # -------------------------------------------------------------------------
    @staticmethod
    def organisation_selector(organisations, widget_id=None):
        """
            Args:
                organisations: all permitted organisations, Rows
                organisation_id: the default organisation ID

            Returns:
                dict of view elements
        """

        #organisations = {id: Row(id, name), _default: id}

        T = current.T

        # Organisation selection buttons
        buttons = []
        default = None
        for k, v in organisations.items():
            if k == "_default":
                default = v
            else:
                button = A(T(v.name),
                           _class = "secondary button org-select",
                           data = {"id": s3_str(k), "name": s3_str(v.name)},
                           )
                buttons.append(button)

        # Organisation name
        classes = ["org-header"]
        data = {}
        if buttons:
            if default:
                organisation = organisations[default]
                data["selected"] = organisation.id
                org_name = organisation.name
                classes.append("selected")
                if len(buttons) == 1:
                    classes.append("disabled")
            else:
                org_name = T("Please select an organization")
        else:
            # No organisations selectable
            org_name = T("No organization")
            classes.append("empty")
            classes.append("disabled")

        # Organisation header
        header = DIV(H4(org_name, _class="org-name"),
                     _class = " ".join(classes),
                     _id = "%s-org-header" % widget_id,
                     data = data,
                     )

        # Organisation selector
        if buttons:
            select = DIV(buttons,
                         _class="button-group stacked hide org-select",
                         _id = "%s-org-select" % widget_id,
                         )
        else:
            select = ""

        return {"org_header": header,
                "org_select": select,
                }

    # -------------------------------------------------------------------------
    @staticmethod
    def event_type_selector(event_types, widget_id=None):
        """
            Args:
                event_types: all permitted event types, Rows
                event_type_id: the default event type ID

            Returns:
                dict of view elements
        """
        # TODO update docstring

        T = current.T

        # Organisation selection buttons
        buttons = []
        default = None
        for k, v in event_types.items():
            if k == "_default":
                default = v
            else:
                name = T(v.name)
                button = A(name,
                           _class = "secondary button event-type-select",
                           # TODO add register_multiple
                           data = {"code": s3_str(v.code),
                                   "name": s3_str(name),
                                   "multiple": "T" if v.register_multiple else "F",
                                   },
                           )
                buttons.append(button)

        data = {}
        classes = ["event-type-header"]
        if buttons:
            if default:
                event_type = event_types.get(default)
                name = T(event_type.name)
                data["code"] = event_type.code
                if len(buttons) == 1:
                    classes.append("disabled")
            else:
                name = T("Please select an event type")
        else:
            name = T("No event types available")
            classes.append("empty")
            classes.append("disabled")

        header = DIV(H4(name, _class="event-type-name"),
                     data = data,
                     _class = " ".join(classes),
                     _id = "%s-event-type-header" % widget_id,
                     )

        select = DIV(buttons,
                     _class="button-group stacked hide event-type-select",
                     _id="%s-event-type-select" % widget_id,
                     )

        return {"event_type_header": header,
                "event_type_select": select,
                }

    # -------------------------------------------------------------------------
    @classmethod
    def get_organisations(cls):
        """
            Looks up all organisations the user is permitted to register
            case events for

            Returns:
                Rows (org_organisation)
        """

        db = current.db
        s3db = current.s3db

        otable = s3db.org_organisation

        permissions = current.auth.permission
        permitted_realms = permissions.permitted_realms("dvr_case_event", "create")
        if permitted_realms is not None:
            query = (otable.pe_id.belongs(permitted_realms)) & \
                    (otable.deleted == False)
        else:
            query = (otable.deleted == False)

        rows = db(query).select(otable.id, otable.name)

        organisations = {row.id: row for row in rows}

        if len(rows) > 1:
            default = cls.get_default_organisation()
            if default and default in organisations:
                organisations["_default"] = default
        elif rows:
            organisations["_default"] = rows.first().id

        return organisations

    # -------------------------------------------------------------------------
    @classmethod
    def get_default_organisation(cls):
        # TODO docstring

        person_id = current.auth.s3_logged_in_person()
        if not person_id:
            return None

        organisation_id = cls.get_current_site_org(person_id)
        if not organisation_id:
            organisation_id = cls.get_employer_org(person_id)

        return organisation_id

    # -------------------------------------------------------------------------
    @staticmethod
    def get_current_site_org(person_id):
        # TODO docstring

        site_id = SitePresence.get_current_site(person_id)

        table = current.s3db.org_site
        query = (table.site_id == site_id)
        row = current.db(query).select(table.organisation_id,
                                       limitby = (0, 1),
                                       ).first()

        return row.organisation_id if row else None

    # -------------------------------------------------------------------------
    @staticmethod
    def get_employer_org(person_id):
        # TODO docstring

        htable = current.s3db.hrm_human_resource

        query = (htable.person_id == person_id) & \
                (htable.status == 1) & \
                (htable.deleted == False)
        row = current.db(query).select(htable.organisation_id,
                                       orderby = ~htable.created_on,
                                       limitby = (0, 1),
                                       ).first()

        return row.organisation_id if row else None

    # -------------------------------------------------------------------------
    @classmethod
    def get_event_type(cls, code, organisation_id):
        # TODO docstring

        db = current.db
        s3db = current.s3db

        # TODO enforce role_required
        # TODO use default type when no code given
        ttable = s3db.dvr_case_event_type
        query = (ttable.code == code) & \
                (ttable.organisation_id == organisation_id) & \
                (ttable.event_class.belongs(cls.EVENT_CLASSES)) & \
                (ttable.is_inactive == False) & \
                (ttable.deleted == False)

        return db(query).select(ttable.id,
                                ttable.register_multiple,
                                limitby = (0, 1),
                                ).first()

    # -------------------------------------------------------------------------
    @classmethod
    def get_event_types(cls, organisation_id=None):
        # TODO docstring

        db = current.db
        s3db = current.s3db

        table = s3db.dvr_case_event_type
        query = current.auth.s3_accessible_query("read", "dvr_case_event_type") & \
                (table.organisation_id == organisation_id) & \
                (table.is_inactive == False) & \
                (table.event_class.belongs(cls.EVENT_CLASSES))

        # Roles required
        sr = current.auth.get_system_roles()
        roles = current.session.s3.roles
        if sr.ADMIN not in roles:
            query &= (table.role_required == None) | \
                     (table.role_required.belongs(roles))

        query &= (table.deleted == False)

        rows = db(query).select(table.id,
                                table.code,
                                table.name,
                                table.is_default,
                                table.min_interval,
                                table.max_per_day,
                                table.register_multiple,
                                #table.comments,
                                )
        event_types = {row.id: row for row in rows}
        for row in rows:
            if row.is_default:
                event_types["_default"] = row.id
                break

        return event_types

    # -------------------------------------------------------------------------
    def permitted(self):
        """
            Helper function to check permissions

            Returns:
                True if permitted to use this method, else False
        """
        # TODO refactor
        # - take action, tablename, organisation_id as parameters
        # - check permitted realms for action on tablename contains organisation
        # - if no organisation is provided, just check the action+tablename
        # - if a record_id is provided, check permission for this record rather than org

        # User must be permitted to create case events
        return self._permitted("create")

    # -------------------------------------------------------------------------
    def get_blocked_events(self, person_id, organisation_id, event_type_id=None, serializable=True):
        """
            Check minimum intervals between consecutive registrations
            of the same event type

            Args:
                person_id: the person record ID
                type_id: check only this event type (rather than all types)

            Returns:
                a dict with blocked event types
                    {type_id: (error_message, blocked_until_datetime|None)}
        """
        # TODO update docstring

        now = current.request.utcnow.replace(microsecond=0)
        day_start = now.replace(hour=0, minute=0, second=0)

        # Get event types for organisation
        event_types = self.get_event_types(organisation_id)

        # Get event types to check
        event_type_ids = set(event_types.keys())
        event_type_ids.discard("_default")
        if event_type_id and event_type_id in event_type_ids:
            check = {event_type_id}
        else:
            check = event_type_ids

        excluded = {}

        # Exclude event types that are not combinable with other events
        # registered today
        non_combinable = self.check_non_combinable(person_id, check, day_start, event_types)
        excluded.update(non_combinable)
        check -= set(non_combinable.keys())

        # Exclude event types for which maximum number of occurences per
        # day have been reached
        max_per_day = self.check_max_per_day(person_id, check, day_start, event_types)
        excluded.update(max_per_day)
        check -= set(max_per_day.keys())

        # Exclude event types for which minimum interval between consecutive
        # occurences has not yet been reached
        min_interval = self.check_min_interval(person_id, check, now, event_types)
        excluded.update(min_interval)
        check -= set(min_interval.keys())

        if serializable:
            output = {}
            for type_id, reason in excluded.items():
                msg, earliest = reason
                event_type = event_types[type_id]
                output[event_type.code] = [s3_str(msg), earliest.isoformat() + "Z"]
            excluded = output

        #print(excluded)

        return excluded

    # -------------------------------------------------------------------------
    @staticmethod
    def check_non_combinable(person_id, check, day_start, event_types):
        # TODO docstring

        T = current.T
        db = current.db
        s3db = current.s3db

        etable = s3db.dvr_case_event
        xtable = s3db.dvr_case_event_exclusion

        event_type_ids = set(event_types.keys())
        event_type_ids.discard("_default")

        # Event type IDs that have been registered for the person today
        query = (etable.person_id == person_id) & \
                (etable.type_id.belongs(event_type_ids)) & \
                (etable.date >= day_start) & \
                (etable.deleted == False)
        registered_today = db(query)._select(etable.type_id, distinct=True)

        query = (xtable.type_id.belongs(check)) & \
                (xtable.excluded_by_id.belongs(registered_today)) & \
                (xtable.deleted == False)
        rows = db(query).select(xtable.type_id,
                                xtable.excluded_by_id,
                                )

        exclusions = {}
        for row in rows:
            type_id = row.type_id
            if type_id in exclusions:
                exclusions[type_id].add(row.excluded_by_id)
            else:
                exclusions[type_id] = {row.excluded_by_id}

        exclude = {}
        next_day = day_start + datetime.timedelta(days=1)
        for type_id, excluded_by in exclusions.items():
            names = ", ".join(s3_str(T(event_types[i].name)) for i in excluded_by)
            msg = T("%(event)s already registered today, not combinable") % \
                   {"event": names}
            exclude[type_id] = (msg, next_day)

        return exclude

    # -------------------------------------------------------------------------
    @staticmethod
    def check_max_per_day(person_id, check, day_start, event_types):
        # TODO docstring

        T = current.T
        db = current.db
        s3db = current.s3db

        etable = s3db.dvr_case_event
        ttable = s3db.dvr_case_event_type

        # Number of registrations for each of check today
        # Where number of registrations is greater than max_per_day
        join = ttable.on((ttable.id == etable.type_id) & \
                         (ttable.max_per_day != None))
        query = (etable.person_id == person_id) & \
                (etable.type_id.belongs(check)) & \
                (etable.date >= day_start) & \
                (etable.deleted == False)
        count = etable.id.count()
        rows = db(query).select(ttable.id,
                                ttable.max_per_day,
                                count,
                                groupby = ttable.id,
                                having = (count >= ttable.max_per_day),
                                join = join,
                                )
        exclude = {}
        next_day = day_start + datetime.timedelta(days=1)
        for row in rows:
            number = row[count]
            type_id = row.dvr_case_event_type.id
            event_type = event_types[type_id]

            if number > 1:
                msg = T("%(event)s already registered %(number)s times today") % \
                       {"event": T(event_type.name), "number": number}
            else:
                msg = T("%(event)s already registered today") % \
                       {"event": T(event_type.name)}
            exclude[type_id] = (msg, next_day)

        return exclude

    # -------------------------------------------------------------------------
    @staticmethod
    def check_min_interval(person_id, check, now, event_types):
        # TODO docstring

        T = current.T
        db = current.db
        s3db = current.s3db

        etable = s3db.dvr_case_event
        ttable = s3db.dvr_case_event_type

        # Last registration of types with a minimum interval
        join = ttable.on((ttable.id == etable.type_id) & \
                         (ttable.max_per_day != None))
        query = (etable.person_id == person_id) & \
                (etable.type_id.belongs(check)) & \
                (etable.deleted == False)
        interval = ttable.min_interval.max()
        last_reg = etable.date.max()
        rows = db(query).select(ttable.id,
                                interval,
                                last_reg,
                                groupby = ttable.id,
                                join = join,
                                )

        exclude = {}
        represent = etable.date.represent
        for row in rows:
            type_id = row.dvr_case_event_type.id
            event_type = event_types[type_id]

            latest, hours = row[last_reg], row[interval]
            if latest and hours:
                earliest = latest + datetime.timedelta(hours=hours)
            else:
                continue

            if earliest > now:
                msg = T("%(event)s already registered on %(timestamp)s") % \
                      {"event": T(event_type.name), "timestamp": represent(latest)}
                exclude[type_id] = (msg, earliest)

        return exclude

    # -------------------------------------------------------------------------
    def get_family_members(self, person, organisation_id, include_ids=False):
        # TODO refactor / cleanup
        """
            Get infos for all family members of person

            Args:
                person: the person (Row)
                include_ids: include the person record IDs

            Returns:
                array with family member infos, format:
                            [{i: the person record ID (if requested)    # TODO Change to "id"
                              l: pe_label,
                              n: fullname,
                              d: dob_formatted,
                              p: picture_URL,           # TODO change to i(mage)
                              r: {
                                event_code: {
                                    m: message,
                                    e: earliest_date_ISO
                                }
                              }, ...
                             ]
        """

        db = current.db
        s3db = current.s3db

        ptable = s3db.pr_person
        itable = s3db.pr_image
        gtable = s3db.pr_group
        mtable = s3db.pr_group_membership
        ctable = s3db.dvr_case
        stable = s3db.dvr_case_status

        # Get all case groups this person belongs to
        person_id = person.id
        query = ((mtable.person_id == person_id) & \
                 (mtable.deleted != True) & \
                 (gtable.id == mtable.group_id) & \
                 (gtable.group_type == 7))
        rows = db(query).select(gtable.id)
        group_ids = set(row.id for row in rows)

        members = {}

        if group_ids:
            join = [ptable.on(ptable.id == mtable.person_id),
                    ctable.on((ctable.person_id == ptable.id) & \
                              (ctable.organisation_id == organisation_id) & \
                              (ctable.archived == False) & \
                              (ctable.deleted == False)),
                    ]

            left = [stable.on(stable.id == ctable.status_id),
                    itable.on((itable.pe_id == ptable.pe_id) & \
                              (itable.profile == True) & \
                              (itable.deleted == False)),
                    ]

            query = (mtable.group_id.belongs(group_ids)) & \
                    (mtable.deleted != True) & \
                    (stable.is_closed != True)
            rows = db(query).select(ptable.id,
                                    ptable.pe_label,
                                    ptable.first_name,
                                    ptable.last_name,
                                    ptable.date_of_birth,
                                    itable.image,
                                    join = join,
                                    left = left,
                                    )

            for row in rows:
                member_id = row.pr_person.id
                if member_id not in members:
                    members[member_id] = row

        output = []

        if members:

            # All event types and blocking rules
            event_types = self.get_event_types()
            #intervals = self.get_interval_rules(set(members.keys()))

            for member_id, data in members.items():

                member = data.pr_person
                picture = data.pr_image

                # Person data
                data = {"l": member.pe_label,
                        "n": s3_fullname(member),
                        "d": S3DateTime.date_represent(member.date_of_birth),
                        }

                # Record ID?
                if include_ids:
                    data["id"] = member_id

                # Profile picture URL
                if picture.image:
                    data["p"] = URL(c = "default",
                                    f = "download",
                                    args = picture.image,
                                    )

                # Blocking rules
                #person_id, organisation_id, event_type_id=None, serializable=True
                event_rules = self.get_blocked_events(member_id,
                                                      organisation_id,
                                                      serializable = True,
                                                      )
                #event_rules = intervals.get(member_id)
                if event_rules:
                    #rules = {}
                    #for event_type_id, rule in event_rules.items():
                        #code = event_types.get(event_type_id).code
                        #rules[code] = (s3_str(rule[0]),
                                       #"%sZ" % s3_encode_iso_datetime(rule[1]),
                                       #)
                    data["r"] = event_rules

                # Add info to output
                output.append(data)

        return output

    # -------------------------------------------------------------------------
    @staticmethod
    def inject_js(widget_id, options):
        """
            Injects required static JS and the instantiation if the
            eventRegistration widget

            Args:
                widget_id: the node ID of the <form> to instantiate
                           the eventRegistration widget on
                options: dict of widget options (JSON-serializable)
        """

        s3 = current.response.s3
        appname = current.request.application

        # Static JS
        scripts = s3.scripts
        if s3.debug:
            script = "/%s/static/scripts/S3/s3.ui.checkpoint.js" % appname
        else:
            script = "/%s/static/scripts/S3/s3.ui.checkpoint.min.js" % appname
        scripts.append(script)

        # Instantiate widget
        scripts = s3.jquery_ready
        script = '''$('#%(id)s').checkPoint(%(options)s)''' % \
                 {"id": widget_id, "options": json.dumps(options)}
        if script not in scripts:
            scripts.append(script)

# END =========================================================================
