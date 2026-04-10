from copy import deepcopy
import hmac
import json
import os
import time

import streamlit as st

import config


# Simple password screen for respondents (note: only very basic authentication!)
# Based on https://docs.streamlit.io/knowledge-base/deploy/authentication-without-sso
# To add advanced respondent authentication to the interview application, see
# https://docs.streamlit.io/develop/concepts/connections/authentication)
def check_password():
    """Check credentials entered via Streamlit widgets.

    This function renders a simple login form (username + password) and checks the
    submitted credentials against either:

    - a random-ID scheme (when config.RANDOM_IDS is True), or
    - a username/password mapping stored in st.secrets.passwords.

    The actual validation happens in the password_entered callback, which:

    - validates integer-based IDs if config.RANDOM_IDS is True,
    - sets st.session_state.password_correct to True or False,
    - stores any validation error message in st.session_state.login_error,
    - deletes st.session_state.password after checking.

    Any error messages are displayed directly below the login form on the next
    rerun of the app.

    Returns
    -------
    tuple[bool, str]
        A tuple (password_correct, username), where:

        - password_correct is True if the user has already entered correct
          credentials in the current session; otherwise False.
        - username is the current value of st.session_state.username (may be
          an empty string if the user has not entered it yet).

    Notes
    -----
    This function is designed to be called near the top of the Streamlit app script.
    It does not call st.stop() for validation errors; instead, it re-renders the
    login form with an error message.
    """

    def login_form():
        """Render the login form to collect username and password."""
        with st.form("Credentials"):
            st.text_input("Username", key="username")
            st.text_input("Password", type="password", key="password")
            st.form_submit_button("Log in", on_click=password_entered)

    def password_entered():
        """Validate username and password and update session state.

        Side effects
        ------------
        - Validates integer-based IDs if config.RANDOM_IDS is True.
        - Sets st.session_state.password_correct to True or False.
        - Stores error messages in st.session_state["login_error"].
        - Deletes st.session_state.password from session state.
        """
        # Clear any previous error
        st.session_state.pop("login_error", None)

        # Store username without whitespaces
        st.session_state.username = (st.session_state.get("username") or "").strip()

        # If RANDOM_IDS is True, check if username and password are integers
        if config.RANDOM_IDS:
            try:
                int(st.session_state.username)
                int(st.session_state.password)
            except ValueError:
                st.session_state.login_error = "Username and password must be integers."
                st.session_state.password_correct = False
                st.session_state.pop("password", None)
                return

        # If RANDOM_IDS is False, check if username is alphanumeric
        else:
            if not st.session_state.username.isalnum():
                st.session_state.login_error = "Username must be alphanumeric."
                st.session_state.password_correct = False
                st.session_state.pop("password", None)
                return

        # Check password in case that usernames are shared via random ID in survey,
        # or when usernames are shared separately and stored in the secrets file
        if (
            config.RANDOM_IDS
            and int(st.session_state.password)
            == config.RANDOM_IDS_PW_ALPHA
            + int(st.session_state.username) * config.RANDOM_IDS_PW_BETA
            or not config.RANDOM_IDS
            and (
                st.session_state.username in st.secrets.passwords
                and hmac.compare_digest(
                    st.session_state.password,
                    st.secrets.passwords[st.session_state.username],
                )
            )
        ):
            st.session_state.password_correct = True
            # Clear any previous error on successful login
            st.session_state.pop("login_error", None)
        else:
            st.session_state.password_correct = False
            # Only set a generic error if we don't already have a more specific one
            st.session_state.setdefault("login_error", "User or password incorrect.")

        # Don't store password in session state
        st.session_state.pop("password", None)

    # If already logged in
    if st.session_state.get("password_correct", False):
        return True, st.session_state.username
    # Otherwise show login form
    login_form()

    # Show any error directly under the form
    if "login_error" in st.session_state:
        st.error(st.session_state.login_error)

    return False, st.session_state.get("username", "")


def add_messages_to_api_kwargs(api, client_kwargs):
    """Adds messages from streamlit session state to client kwargs in a format suitable
    for the specified API.

    Different LLM APIs have different conventions for passing conversation history and
    system prompts. This function creates a deep copy of the provided kwargs and
    messages, and modifies them as needed.

    - OpenAI: Stores st.session_state.messages as "input" and prepends a developer
      message to include the system prompt.
    - Azure: Stores st.session_state.messages as "messages" and prepends a system
      message to include the system prompt.
    - Google: Stores st.session_state.messages as "contents", prepends a dummy "Hello"
      user message, renames "assistant" role to "model", and restructures message
      content into the {"parts": [{"text": ...}]} format.
    - Anthropic: Stores st.session_state.messages as "messages" and prepends a dummy
      "Hello" user message (required by the API to have a user message before the first
      assistant response).

    Parameters
    ----------
    api : str
        The API identifier: "openai", "azure", "google", or "anthropic".
    client_kwargs : dict
        Keyword arguments intended for the API call, expected to contain a
        "messages" key with a list of message dictionaries.

    Returns
    -------
    dict
        A deep copy of client_kwargs with API-specific transformations applied.

    Raises
    ------
    ValueError
        If api is not one of the supported values.
    """

    # Copy client kwargs and add messages from session state
    client_kwargs_transformed = deepcopy(client_kwargs)
    client_kwargs_transformed["messages"] = deepcopy(st.session_state.messages)

    # For the OpenAI API, rename "messages" to "input" and add system prompt
    if api == "openai":
        client_kwargs_transformed["input"] = client_kwargs_transformed.pop("messages")
        client_kwargs_transformed["input"].insert(
            0, {"role": "developer", "content": config.SYSTEM_PROMPT}
        )

    # For the Azure API, add system prompt
    elif api == "azure":
        client_kwargs_transformed["messages"].insert(
            0, {"role": "system", "content": config.SYSTEM_PROMPT}
        )

    # For the Google API, rename "messages" to "contents", add required initial user
    # message, and restructure messages
    elif api == "google":
        client_kwargs_transformed["contents"] = client_kwargs_transformed.pop(
            "messages"
        )
        client_kwargs_transformed["contents"].insert(
            0, {"role": "user", "content": "Hello"}
        )

        # Rename "assistant" role to "model" and restructure content
        for message in client_kwargs_transformed["contents"]:
            role = message.get("role")
            if role == "assistant":
                message["role"] = "model"
            message["parts"] = [{"text": message.pop("content")}]

    # For the Anthropic API, add required initial user message
    elif api == "anthropic":
        client_kwargs_transformed["messages"].insert(
            0, {"role": "user", "content": "Hello"}
        )
    # For Mistral same as Azure
    elif api == "mistral":
        client_kwargs_transformed["messages"].insert(
            0, {"role": "system", "content": config.SYSTEM_PROMPT}
        )

    else:
        raise ValueError(f"Unknown API: {api}")

    return client_kwargs_transformed


def iter_text_deltas(client, client_kwargs):
    """Yield text deltas from the configured chat API.

    This is a generator that abstracts over different providers (OpenAI, Anthropic,
    Google, and Azure) and yields chunks of text as they arrive from the streaming API.

    Parameters
    ----------
    client
        A client instance corresponding to the selected API:
        - OpenAI client if config.API == "openai"
        - Anthropic client if config.API == "anthropic"
        - Google client if config.API == "google"
        - Azure client if config.API == "azure"
    client_kwargs : dict
        Keyword arguments passed directly to the underlying streaming method
        (e.g. client.chat.completions.create(**client_kwargs)).

    Yields
    ------
    str
        Successive pieces of the message text (deltas) as they are streamed.

    Raises
    ------
    ValueError
        If config.API is not one of "openai", "anthropic", "google", or
        "azure".
    """

    # Transform messages in client kwargs as needed for different APIs
    client_kwargs_transformed = add_messages_to_api_kwargs(config.API, client_kwargs)

    # OpenAI API
    if config.API == "openai":
        stream = client.responses.create(**client_kwargs_transformed)
        for event in stream:
            if event.type == "response.output_text.delta":
                yield event.delta

    # Anthropic API
    elif config.API == "anthropic":
        with client.messages.stream(**client_kwargs_transformed) as stream:
            for delta in stream.text_stream:
                if delta:
                    yield delta

    # Google API
    elif config.API == "google":
        for chunk in client.models.generate_content_stream(**client_kwargs_transformed):
            delta = getattr(chunk, "text", None)
            if delta:
                yield delta

    # Azure API
    elif config.API == "azure":
        for update in client.complete(**client_kwargs_transformed):
            if getattr(update, "choices", None):
                delta = update.choices[0].delta.content
                if delta:
                    yield delta

    elif config.API == "mistral":
        stream = client.chat.stream(**client_kwargs_transformed)
        for chunk in stream:
            delta= chunk.data.choices[0].delta.content
            if delta:
                yield delta

    else:
        raise ValueError(f"Unknown API: {config.API}")


def stream_response(client, client_kwargs, message_placeholder, minimum_characters=5):
    """Stream a chat response and update a Streamlit placeholder.

    This function uses :func:`iter_text_deltas` to stream text from the chat API and
    progressively updates a `st.empty()` placeholder in the UI. It optionally stops
    early if a closing code defined in config.CLOSING_MESSAGES appears in the
    streamed text.

    Parameters
    ----------
    client
        Chat client instance (OpenAI, Anthropic, or Azure).
    client_kwargs : dict
        Keyword arguments used to initiate the streaming API call, including the
        messages to send.
    message_placeholder : streamlit.delta_generator.DeltaGenerator
        A placeholder created by st.empty() or similar, used to render the
        streaming text via .markdown(...).
    minimum_characters : int, optional
        Minimum number of characters to accumulate before rendering any text, to
        avoid quickly flashing short control codes. Default is 5.

    Returns
    -------
    str
        The full interviewer message assembled from all streamed deltas.

    Side effects
    ------------
    - Updates the Streamlit UI via message_placeholder.markdown(...).
    - May clear the placeholder with message_placeholder.empty() if a closing
      code is detected.
    """

    # Initialise message of interviewer
    message_interviewer = ""
    stopped_on_code = False

    # Store closing codes once to avoid repeated lookups
    closing_codes = config.CLOSING_MESSAGES.keys()

    # Iterate over text deltas from the chat API
    for delta in iter_text_deltas(client, client_kwargs):
        message_interviewer += delta

        # Start displaying once minimum characters reached (to avoid displaying codes)
        if len(message_interviewer) > minimum_characters:
            message_placeholder.markdown(message_interviewer + "▌")

        # Early exit on closing code
        if any(code in message_interviewer for code in closing_codes):
            message_placeholder.empty()
            stopped_on_code = True
            break

    # Final render (no cursor) only if we didn't stop on a code
    if not stopped_on_code and message_interviewer:
        message_placeholder.markdown(message_interviewer)

    return message_interviewer


# Functions to load and save backups, transcripts, and metadata:


def load_backup(backups_directory):
    """Load backup data for the current user from disk, if available.

    The backup is expected to be a JSON file named <username>.json in the given
    directory. The structure is assumed to be:

    .. code-block:: json

        {
          "messages": [...],
          "start_time": <float>,
          "times": [...],
          "num_text_and_voice_answers": [<int>, <int>]
        }

    If the file does not exist or cannot be read, sensible defaults for a new
    interview session are returned.

    Parameters
    ----------
    backups_directory : str | os.PathLike
        Directory where backup JSON files are stored.

    Returns
    -------
    tuple
        A 4-tuple (messages, start_time, times, num_text_and_voice_answers) where:

        messages : list[dict]
            List of message dictionaries as used in the chat history. If no assistant
            messages are found, this is an empty list and represents a fresh start.
        start_time : float
            Epoch timestamp (in seconds) of when the interview started.
        times : list[float]
            List of durations (in minutes) spent in previous logins.
        num_text_and_voice_answers : list[int]
            A list [num_text, num_voice] counting the number of text and voice
            answers given by the respondent.

    Notes
    -----
    - If at least one message from the assistant is present, only messages up to and
      including the last assistant message are returned.
    - On any exception (file missing, JSON error, etc.), a new interview state is
      returned with start_time = time.time(), empty messages and times, and
      [0, 0] as the answer counts.
    """

    # Try to load backup data if it exists
    try:
        with open(
            os.path.join(
                backups_directory,
                f"{st.session_state.username}.json",
            ),
            "r",
        ) as f:
            data = json.load(f)

        # Number of user answers given by text vs voice input
        num_text_and_voice_answers = data["num_text_and_voice_answers"]

        # Start time
        start_time = data["start_time"]

        # The next element contains information on times spent using the dashboard
        times = data["times"]

        # The remaining items are messages
        messages = data["messages"]

        # Check if at least one message from "assistant" is in backup. If yes, return
        # all messages until and including the last assistant message.
        # If not, return empty lists for start of interview.
        if any(message["role"] == "assistant" for message in messages):
            last_assistant_index = (
                len(messages)
                - 1
                - [message["role"] == "assistant" for message in messages[::-1]].index(
                    True
                )
            )
            messages = messages[: last_assistant_index + 1]
        else:
            messages = []
            start_time = time.time()
            times = []
            num_text_and_voice_answers = [0, 0]

    # If no backup data exists, return empty lists for start of interview
    except Exception:
        messages = []
        start_time = time.time()
        times = []
        num_text_and_voice_answers = [0, 0]

    return messages, start_time, times, num_text_and_voice_answers


def save_backup(backups_directory, admin_alias):
    """Save the most recent interview data to disk as a JSON backup.

    The file is written as <username>.json into the specified directory and
    contains:

    - messages: full message history (copied from st.session_state.messages)
    - start_time: initial interview start time
    - times: list of durations (in minutes) across logins
    - num_text_and_voice_answers: counts of text vs voice answers

    Data for users whose username equals admin_alias is not written.

    Parameters
    ----------
    backups_directory : str | os.PathLike
        Directory where the backup JSON file should be written.
    admin_alias : str, optional
        Username for which backups should be suppressed (e.g. admin/test accounts).
        Default is "testaccount".

    Returns
    -------
    None
        The function is called for its side effects.

    Side effects
    ------------
    - Reads from st.session_state (messages, times, username, etc.).
    - Writes <username>.json into backups_directory.
    """

    # Don't store data for admin alias
    if st.session_state.username != admin_alias:
        # Empty dictionary
        data = {}

        # Add messages
        data["messages"] = st.session_state.messages.copy()

        # Add start time
        data["start_time"] = st.session_state.start_time

        # Add time spent using dashboard for interview across different logins
        current_time = time.time()
        times = st.session_state.times_previous_attempts + [
            (current_time - st.session_state.start_time_current_login) / 60
        ]
        data["times"] = times

        # Add number of answers through text and voice inputs
        data["num_text_and_voice_answers"] = st.session_state.num_text_and_voice_answers

        # Save all as JSON
        with open(
            os.path.join(
                backups_directory,
                f"{st.session_state.username}.json",
            ),
            "w",
        ) as f:
            json.dump(data, f)


def save_transcript_and_metadata(
    transcripts_directory,
    metadata_directory,
    api_kwargs,
    admin_alias,
):
    """Write the interview transcript and metadata to disk for the current user.

    Two files are written (unless the username equals admin_alias):

    1. Transcript file: <username>.txt in transcripts_directory
       - Contains alternating "Interviewer:" and "Respondent:" lines.
       - Skips system messages and any assistant messages that contain closing codes
         defined in config.CLOSING_MESSAGES.

    2. Metadata file: <username>.txt in metadata_directory
       - Contains start and end times (UTC), durations per login and in total,
         counts of text and voice respondent messages, counts of all respondent and
         interviewer messages, API call argument, and system prompt.

    Parameters
    ----------
    transcripts_directory : str | os.PathLike
        Directory where the transcript `.txt` file will be written.
    metadata_directory : str | os.PathLike
        Directory where the metadata `.txt` file will be written.
    api_kwargs : dict
        Keyword arguments used in API call.
    admin_alias : str, optional
        Username for which no transcript or metadata should be stored.
        Default is "testaccount".

    Returns
    -------
    None
        The function is called for its side effects.

    Side effects
    ------------
    - Reads from st.session_state (messages, times, counters, start time, etc.).
    - Writes two text files into the specified directories.
    """

    # Don't store data for admin alias
    if st.session_state.username != admin_alias:
        with open(
            os.path.join(
                transcripts_directory,
                f"{st.session_state.username}.txt",
            ),
            "w",
        ) as f:
            user_messages = 0
            assistant_messages = 0
            for message in st.session_state.messages:
                if message["role"] == "assistant" and any(
                    code in message["content"]
                    for code in config.CLOSING_MESSAGES.keys()
                ):
                    # Skip messages with codes
                    continue
                elif message["role"] == "assistant":
                    assistant_messages += 1
                    f.write(f"Interviewer: {message['content']}\n\n")
                elif message["role"] == "user":
                    user_messages += 1
                    f.write(f"Respondent: {message['content']}\n\n")

            # Store file with start time, duration of interview, number of text vs voice
            # answers, and total messages from respondent and interviewer
            current_time = time.time()
            times = st.session_state.times_previous_attempts + [
                (current_time - st.session_state.start_time_current_login) / 60
            ]

            times_str = [f"{d:.2f}" for d in times]
            total_time = sum(times)

            if len(times) > 1:
                # e.g. "3.50 + 2.25 = 5.75 minutes"
                times_display = " + ".join(times_str) + f" = {total_time:.2f}"
            else:
                # e.g. "3.50 minutes"
                times_display = times_str[0]

            # Next, document API kwargs w/o messages and system prompt (as these are
            # stored separately)
            # For the Google API, system_instruction is stored in a config dictionary
            # For the Anthropic API, the system prompt is stored as the parameter system
            # For the OpenAI/Azure, the system prompt is part of the messages and thus
            # excluded automatically since messages are not parts of api_kwargs and only
            # added before each API call through add_messages_to_api_kwargs()
            api_kwargs_text = json.dumps(
                {
                    k: (
                        {sk: sv for sk, sv in v.items() if sk != "system_instruction"}
                        if k == "config" and isinstance(v, dict) and len(v) > 1
                        else v
                    )
                    for k, v in api_kwargs.items()
                    if k != "system"
                    and not (
                        k == "config"
                        and isinstance(v, dict)
                        and len(v) == 1
                        and "system_instruction" in v
                    )
                },
                indent=2,
            )

            meta_text = f"""Start time (UTC): {time.strftime('%d/%m/%Y %H:%M:%S', time.gmtime(st.session_state.start_time))}
End time (UTC): {time.strftime('%d/%m/%Y %H:%M:%S', time.gmtime(current_time))}
Interview duration (multiple logins are separated by '+'): {times_display} minutes
Text answers given by respondent: {st.session_state.num_text_and_voice_answers[0]}
Voice answers given by respondent: {st.session_state.num_text_and_voice_answers[1]}
Total respondent messages: {user_messages}
Total interviewer messages: {assistant_messages}

---

API call parameters at the time of concluding the interview (excluding messages and system prompt):

{api_kwargs_text}

---

System prompt at the time of concluding the interview:

{config.SYSTEM_PROMPT}
"""

            with open(
                os.path.join(metadata_directory, f"{st.session_state.username}.txt"),
                "w",
            ) as d:
                d.write(meta_text)


def is_transcript_saved(transcripts_directory):
    """Check whether a transcript file for the current user already exists.

    Parameters
    ----------
    transcripts_directory : str | os.PathLike
        Directory where transcript `.txt` files are stored.

    Returns
    -------
    bool
        True if <username>.txt exists in transcripts_directory for the
        current st.session_state.username, otherwise False.
    """

    path = os.path.join(transcripts_directory, f"{st.session_state.username}.txt")

    return os.path.exists(path)
