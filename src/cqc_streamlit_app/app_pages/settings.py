#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
import os

import streamlit as st
from cqc_cpcc.utilities.AI import posthog_telemetry as telemetry
from cqc_streamlit_app.app_settings import load_settings, update_settings
from cqc_streamlit_app.initi_pages import init_session_state
from cqc_streamlit_app.utils import page_header, secret_text_input

# Initialize session state variables
init_session_state()


def main():
    page_header("Settings", ":material/settings:",
                "Keys and logins stay on this computer; nothing here is stored online.")

    credentials_tab, models_tab, preferences_tab, analytics_tab = st.tabs(
        ["Credentials", "Models", "Preferences", "Analytics"])
    with credentials_tab:
        credentials_section()
    with models_tab:
        model_settings_section()
    with preferences_tab:
        preferences_section()
    with analytics_tab:
        analytics_settings_section()


def credentials_section():
    """Keys and logins for this browser session (from .env by default)."""
    # No type="password" fields on this page: Safari treats them as a login form
    # and offers to save/fill a password on every Tab (see secret_text_input).
    # Two columns keep the Save button in view at 1280x800 (UX goals: 0 scrolls per tab).
    keys, login = st.columns(2)
    with keys:
        openrouter_api_key = secret_text_input("OpenRouter API key (required)",
                                               st.session_state.openrouter_api_key or "", key="openrouter_api_key")
        openai_api_key = secret_text_input(
            "OpenAI API key (optional)", st.session_state.openai_api_key or "", key="openai_api_key",
            help="Only needed to transcribe audio/video submissions (Whisper); "
                 "get it at https://platform.openai.com/account/api-keys.")
        attendance_tracker_url = st.text_input("Attendance tracker URL",
                                               value=st.session_state.attendance_tracker_url or "",
                                               autocomplete="off", help="ATTENDANCE_TRACKER_URL in .env.")
    with login:
        instructor_user_id = st.text_input("Instructor user ID (required)",
                                           value=st.session_state.instructor_user_id or "", autocomplete="off")
        instructor_password = secret_text_input("Instructor password (required)",
                                                st.session_state.instructor_password or "", key="instructor_password")
        instructor_signature = st.text_input("Instructor signature", value=st.session_state.instructor_signature or "",
                                             autocomplete="off", help="Used at the end of feedback.")

    required_vars = [openrouter_api_key, instructor_user_id, instructor_password]

    # If the 'Save' button is clicked
    if st.button("Save credentials", type="primary", icon=":material/save:"):
        if any(not str(v or "").strip() for v in required_vars):
            st.error("Please provide the missing required settings.")
        else:
            # Set both the st session state and the environment variable for required vars
            st.session_state.openrouter_api_key = os.environ["OPENROUTER_API_KEY"] = openrouter_api_key.strip()
            if openai_api_key and openai_api_key.strip():
                st.session_state.openai_api_key = os.environ["OPENAI_API_KEY"] = openai_api_key.strip()
            st.session_state.instructor_user_id = os.environ["INSTRUCTOR_USERID"] = instructor_user_id.strip()
            st.session_state.instructor_password = os.environ["INSTRUCTOR_PASS"] = instructor_password.strip()

            # Set the the st session state and the environment variable for non-required vars
            if (instructor_signature and instructor_signature.strip()):
                st.session_state.instructor_signature = os.environ["FEEDBACK_SIGNATURE"] = instructor_signature.strip()
            if (attendance_tracker_url and attendance_tracker_url.strip()):
                st.session_state.attendance_tracker_url = os.environ[
                    "ATTENDANCE_TRACKER_URL"] = attendance_tracker_url.strip()

            st.success("Credentials saved for this session.", icon=":material/check_circle:")


def _on_legacy_toggle():
    try:
        update_settings(show_legacy_pages=bool(st.session_state.show_legacy_pages_toggle))
    except OSError as e:
        st.session_state["_preferences_error"] = f"Could not save the preference: {e}"


def preferences_section():
    """Preferences saved to this computer (they survive restarts and git pull)."""
    st.toggle(
        "Show legacy pages",
        value=load_settings().show_legacy_pages,
        key="show_legacy_pages_toggle",
        on_change=_on_legacy_toggle,
        help="Adds a Legacy section to the navigation with pages that are being "
             "retired (currently: Exams (legacy)). Saved to this computer.",
    )
    st.caption("Legacy pages are deprecated and will be removed once they are no longer used.")
    if error := st.session_state.pop("_preferences_error", None):
        st.error(error)


def model_settings_section():
    """Show the registry's model per feature and let the instructor pin a different one.

    A pin sets ``CQC_MODEL_<ROLE>`` for this app process, which every LLM call honours
    (including the preprocessing digest, which has no picker of its own). It lasts until
    the app restarts; put the variable in ``.env`` to keep it.
    """
    from cqc_cpcc.utilities.AI import model_registry
    from cqc_streamlit_app.utils import registry_model_choices

    registry = model_registry.load_registry()
    st.caption(
        f"Defaults come from `config/model_registry.json` (revision {registry.revision}), "
        "which the monthly model evaluation updates."
    )

    rows = []
    for role in model_registry.ROLES:
        resolved = model_registry.resolve(role)
        rows.append({
            "Feature": role,
            "Model in use": resolved.model,
            "Source": resolved.source,
            "Reasoning effort": resolved.reasoning_effort or "default",
            "Fallback": resolved.fallback or "none",
            "Previous": registry.previous.get(role, "none"),
        })
    st.dataframe(rows, hide_index=True)

    unpinned = "Use registry default"
    choices = [unpinned] + registry_model_choices()
    with st.form("model_pins"):
        pins = {}
        for role in model_registry.ROLES:
            current = os.environ.get(f"CQC_MODEL_{role.upper()}")
            options = choices if not current or current in choices else choices + [current]
            pins[role] = st.selectbox(
                f"Pin model for {role}",
                options,
                index=options.index(current) if current in options else 0,
                key=f"model_pin_{role}",
            )
        if st.form_submit_button("Save model pins"):
            for role, choice in pins.items():
                env_key = f"CQC_MODEL_{role.upper()}"
                if choice == unpinned:
                    os.environ.pop(env_key, None)
                else:
                    os.environ[env_key] = choice
            st.success("Model pins saved for this session. Add CQC_MODEL_<FEATURE> to .env to keep them.")
            st.rerun()


_CUSTOM_HOST = "Custom (self-hosted)"


def analytics_settings_section():
    """PostHog credentials, for when they are not in ``.env``. Optional."""
    st.caption(
        "Optional. Sends run counts, durations, token usage and scrubbed errors to your "
        "PostHog project. Student names, IDs, e-mails, submissions and grades are never "
        "sent. Leave the key blank to turn analytics off."
    )

    enabled, reason = telemetry.status()
    (st.success if enabled else st.info)("Analytics status: %s" % reason)

    posthog_api_key = secret_text_input(
        "PostHog project API key",
        st.session_state.posthog_api_key or "",
        key="posthog_api_key",
        help="Project settings > Project API key (starts with phc_). Same as POSTHOG_API_KEY in .env.",
    )

    current_host = st.session_state.posthog_host or telemetry.DEFAULT_HOST
    region_labels = list(telemetry.KNOWN_HOSTS) + [_CUSTOM_HOST]
    known_by_url = {url: label for label, url in telemetry.KNOWN_HOSTS.items()}
    region = st.selectbox(
        "PostHog region",
        region_labels,
        index=region_labels.index(known_by_url.get(current_host, _CUSTOM_HOST)),
        help="Must match the region your PostHog project was created in.",
    )
    if region == _CUSTOM_HOST:
        posthog_host = st.text_input(
            "PostHog host URL",
            value="" if current_host in known_by_url else current_host,
            placeholder="https://posthog.example.edu",
            autocomplete="off",
        )
    else:
        posthog_host = telemetry.KNOWN_HOSTS[region]

    if st.button("Save analytics settings", icon=":material/save:"):
        host = (posthog_host or "").strip()
        if host and not host.startswith("https://"):
            st.error("The PostHog host must be an https:// URL.")
            return
        st.session_state.posthog_api_key = (posthog_api_key or "").strip() or None
        st.session_state.posthog_host = host or None
        enabled, reason = telemetry.configure(st.session_state.posthog_api_key, host or None)
        (st.success if enabled else st.warning)("Analytics status: %s" % reason)


if __name__ == '__main__':
    main()
