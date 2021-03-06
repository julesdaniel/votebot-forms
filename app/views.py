from flask import Blueprint, request, jsonify, current_app
import decorator
from ovr_forms import OVR_FORMS
from ovr_forms.base_ovr_form import OVRError
from ovr_forms.form_utils import clean_sensitive_info, ValidationError
import jobs

votebot = Blueprint('votebot', __name__)


@votebot.errorhandler(OVRError)
def handle_ovr_error(error):
    error_dict = error.to_dict()
    return render_error(
        error.status_code,
        "form_error",
        error_dict["message"],
        error_dict["payload"]
    )

def render_error(status_code, str_code, message=None, payload=None):
    response = jsonify({
            "error": True,
            "status_code": status_code,
            "error_type": str_code,
            "message": message,
            "payload": payload
        })
    response.status_code = status_code
    return response

@votebot.route('/pdf', methods=['POST'])
@decorator.requires_auth
def generate_pdf():
    return registration(request, "generate_pdf")

@votebot.route('/ovr', methods=['POST'])
@decorator.requires_auth
def ovr():
    return registration(request, "ovr")

def registration(request, registration_type="generate_pdf"):
    request_json = request.get_json(force=True)  # so we don't have to set mimetype
    if not "user" in request_json:
        return render_error(400, "missing_user_data", "No user data specified.")

    if not "uid" in request_json:
        return render_error(400, "no_uid", "No unique uid specified in submission.")

    user = request_json['user']

    # pull fields out of user.settings
    if 'settings' in user:
        for (key, value) in user['settings'].items():
            user[key] = value
        del user['settings']

    state = user['state']
    if registration_type == "generate_pdf":
        form = OVR_FORMS['NVRA'](mail_deadline=request_json.get('mail_deadline_text', None))
    elif state in OVR_FORMS:
        form = OVR_FORMS[state]()
    else:
        return render_error(
            500,
            "internal_error",
            "OVR submission specified, but state not implemented."
        )

    form.set_uid(request_json["uid"])

    # validate before queueing for submission
    try:
        form.validate(user)
    except ValidationError, e:
        if hasattr(current_app, 'sentry'):
            current_app.sentry.captureException(e)
            user_filtered = clean_sensitive_info(user)
            current_app.sentry.user_context(user_filtered)
        return render_error(400, "missing_fields", "Missing required fields", e.payload)

    debug_submit = current_app.config.get('DEBUG_SUBMIT', False)
    if debug_submit:
        # return job submit immediately
        return jobs.submit_form(form, user, callback_url=request_json.get('callback_url'))
    else:
        # queue asynchronous form submission via redis
        jobs.submit_form.queue(form, user, callback_url=request_json.get('callback_url'))

        return jsonify({
            'status': 'queued',
            'uid': str(form.get_uid())
        })

@votebot.route('/', methods=['GET'])
def index():
    return jsonify({'application': 'votebot-forms',
                   'status': 'OK'})
