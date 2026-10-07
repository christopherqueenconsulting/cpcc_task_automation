#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
import os

import streamlit as st
from cqc_cpcc.utilities.AI import posthog_telemetry as telemetry
from cqc_streamlit_app.initi_pages import init_session_state
from cqc_streamlit_app.utils import get_cpcc_css, secret_text_input

# Initialize session state variables
init_session_state()


def main():
    st.set_page_config(layout="wide", page_title="Settings", page_icon="⚙️")  # TODO: Change the page icon

    css = get_cpcc_css()
    st.markdown(
        css,
        unsafe_allow_html=True
    )

    # Streamlit app
    st.subheader('Settings')

    st.write(
        'The information entered on this page is not stored online. It is only available in the browser for the other pages to use and run properly')

    # Get API keys
    # No type="password" fields on this page: Safari treats them as a login form
    # and offers to save/fill a password on every Tab (see secret_text_input).
    openrouter_api_key = secret_text_input("Openrouter API Key", st.session_state.openrouter_api_key or "",
                                           key="openrouter_api_key")
    st.caption("*Required for all apps")

    openai_api_key = secret_text_input("OpenAI API Key", st.session_state.openai_api_key or "",
                                       key="openai_api_key")
    st.caption("*Optional: only needed to transcribe audio/video submissions (Whisper); "
               "get it [here](https://platform.openai.com/account/api-keys).*")

    # Get CPCC variables
    instructor_user_id = st.text_input("Instructor User ID", value=st.session_state.instructor_user_id or "",
                                       autocomplete="off")
    st.caption("*Required for all apps")
    instructor_password = secret_text_input("Instructor Password", st.session_state.instructor_password or "",
                                            key="instructor_password")
    st.caption("*Required for all apps")

    instructor_signature = st.text_input("Instructor Signature", value=st.session_state.instructor_signature or "",
                                         autocomplete="off")
    st.caption("Used at end of feedback.")

    attendance_tracker_url = st.text_input("Advanced Tracker URL", value=st.session_state.attendance_tracker_url or "",
                                           autocomplete="off")
    st.caption("URL to the Attendance Tracker (`ATTENDANCE_TRACKER_URL`).")

    required_vars = [openrouter_api_key, instructor_user_id, instructor_password]

    # If the 'Save' button is clicked
    if st.button("Save"):
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

            st.success("Settings Saved")

    analytics_settings_section()
    model_settings_section()


def model_settings_section():
    """Show the registry's model per feature and let the instructor pin a different one.

    A pin sets ``CQC_MODEL_<ROLE>`` for this app process, which every LLM call honours
    (including the preprocessing digest, which has no picker of its own). It lasts until
    the app restarts; put the variable in ``.env`` to keep it.
    """
    from cqc_cpcc.utilities.AI import model_registry
    from cqc_streamlit_app.utils import registry_model_choices

    st.divider()
    st.subheader("AI models")
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
    st.divider()
    st.subheader("Usage Analytics (PostHog)")
    st.write(
        "Optional. Sends run counts, durations, token usage and scrubbed errors to your "
        "PostHog project. Student names, IDs, e-mails, submissions and grades are never "
        "sent. Leave the key blank to turn analytics off."
    )

    enabled, reason = telemetry.status()
    (st.success if enabled else st.info)("Analytics status: %s" % reason)

    posthog_api_key = secret_text_input(
        "PostHog Project API Key",
        st.session_state.posthog_api_key or "",
        key="posthog_api_key",
        help="Project settings > Project API key (starts with phc_). Same as POSTHOG_API_KEY in .env.",
    )

    current_host = st.session_state.posthog_host or telemetry.DEFAULT_HOST
    region_labels = list(telemetry.KNOWN_HOSTS) + [_CUSTOM_HOST]
    known_by_url = {url: label for label, url in telemetry.KNOWN_HOSTS.items()}
    region = st.selectbox(
        "PostHog Region",
        region_labels,
        index=region_labels.index(known_by_url.get(current_host, _CUSTOM_HOST)),
        help="Must match the region your PostHog project was created in.",
    )
    if region == _CUSTOM_HOST:
        posthog_host = st.text_input(
            "PostHog Host URL",
            value="" if current_host in known_by_url else current_host,
            placeholder="https://posthog.example.edu",
            autocomplete="off",
        )
    else:
        posthog_host = telemetry.KNOWN_HOSTS[region]

    if st.button("Save Analytics Settings"):
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
