# Main application for running AI-led interviews
#
# AI interviewer: text
# Respondent: text and/or voice input (as set in config.py)


import os
import random
import time
from copy import deepcopy

import streamlit as st
from openai import OpenAI
from mistralai.client import Mistral

import config
from utils import (
    check_password,
    stream_response,
    save_backup,
    load_backup,
    save_transcript_and_metadata,
    is_transcript_saved,
)


#
# 1. Set up interview environment
#

# Set page title and icon
st.set_page_config(page_title="Interview", page_icon=config.AVATAR_INTERVIEWER)

# Check if login screen is enabled
if config.LOGINS:
    # Check password (displays login screen)
    pwd_correct, username = check_password()
    if not pwd_correct:
        st.stop()
    else:
        st.session_state.username = username
# Otherwise ask the respondent to enter a username without password
else:
    # Until username confirmed, show the input and stop
    if "username" not in st.session_state:
        username_input = st.text_input(
            "Please enter a username to start the interview:",
            key="username_input",
        )
        if not username_input:
            # User hasn't typed anything yet, just render the page
            st.stop()
        if not username_input.strip().isalnum():
            st.warning("Username must be alphanumeric.")
            st.stop()

        # Save non-empty username and rerun
        st.session_state.username = username_input.strip()
        st.rerun()

# Directories
if not os.path.exists(config.TRANSCRIPTS_DIRECTORY):
    os.makedirs(config.TRANSCRIPTS_DIRECTORY)
if not os.path.exists(config.METADATA_DIRECTORY):
    os.makedirs(config.METADATA_DIRECTORY)
if not os.path.exists(config.BACKUPS_DIRECTORY):
    os.makedirs(config.BACKUPS_DIRECTORY)

# Check if interview has been completed and, if so, only display closing message
interview_previously_completed = is_transcript_saved(config.TRANSCRIPTS_DIRECTORY)
if "messages" not in st.session_state and interview_previously_completed:
    st.session_state.messages = []
    st.session_state.interview_active = False

    code_found = False
    try:
        messages, _, _, _ = load_backup(backups_directory=config.BACKUPS_DIRECTORY)
    except Exception:
        messages = []

    # Display closing message
    for code in config.CLOSING_MESSAGES.keys():
        if any(
            code in message["content"]
            for message in messages
            if message["role"] == "assistant"
        ):
            code_found = True
            closing_message = config.CLOSING_MESSAGES[code]
            break

    if not code_found:
        closing_message = "The interview has already been completed."

    st.session_state.interview_active = False
    st.markdown(closing_message)
    st.stop()

# If interview has not yet been completed, initialise session states
if "interview_active" not in st.session_state:
    st.session_state.interview_active = True

# Start time for this login
if "start_time_current_login" not in st.session_state:
    st.session_state.start_time_current_login = time.time()

# Initialise messages, times using dashboard, and the number of voice and text answers
if "messages" not in st.session_state:
    # Returns [], time.time(), [], [0, 0] if no data in the backup directory is found
    (
        st.session_state.messages,
        st.session_state.start_time,
        st.session_state.times_previous_attempts,
        st.session_state.num_text_and_voice_answers,
    ) = load_backup(backups_directory=config.BACKUPS_DIRECTORY)

# Create Boolean to track if transcription of voice input is done
if "transcription_done" not in st.session_state:
    st.session_state.transcription_done = False

# Key for voice input element
if "voice_input_key" not in st.session_state:
    st.session_state.voice_input_key = random.uniform(0, 1)

# Button to cancel interview
col1, col2 = st.columns([0.75, 0.25])
with col2:
    if st.session_state.interview_active and st.button(
        "Quit interview", help="Logging back in will restore the chat."
    ):
        st.session_state.interview_active = False
        quit_message = "You have quit the interview for now."
        # Run before quit message, to not store that but only display it to the user
        save_backup(
            backups_directory=config.BACKUPS_DIRECTORY, admin_alias=config.ADMIN_ALIAS
        )
        st.session_state.messages.append({"role": "assistant", "content": quit_message})
        st.rerun()

# Initialise API kwargs
if isinstance(config.ADDITIONAL_API_KWARGS, dict):
    api_kwargs = deepcopy(config.ADDITIONAL_API_KWARGS)
else:
    raise ValueError(
        "ADDITIONAL_API_KWARGS must be specified as a dictionary in config.py, either empty or containing valid additional API parameters."
    )

# The model parameter is shared across all APIs
api_kwargs["model"] = config.MODEL

# API clients
if config.API == "openai":

    client = OpenAI(api_key=st.secrets["KEY_OPENAI"])
    api_kwargs["stream"] = True

elif config.API == "anthropic":
    import anthropic

    client = anthropic.Anthropic(api_key=st.secrets["KEY_ANTHROPIC"])
    api_kwargs["system"] = config.SYSTEM_PROMPT
    # Set max tokens default if no value set in config.py
    api_kwargs.setdefault("max_tokens", 4096)

elif config.API == "google":
    from google import genai

    client = genai.Client(api_key=st.secrets["KEY_GOOGLE"])
    api_kwargs.setdefault("config", {})["system_instruction"] = config.SYSTEM_PROMPT


elif config.API == "azure":
    from azure.ai.inference import ChatCompletionsClient
    from azure.core.credentials import AzureKeyCredential

    client = ChatCompletionsClient(
        endpoint=st.secrets["ENDPOINT_AZURE"],
        credential=AzureKeyCredential(st.secrets["KEY_AZURE"]),
        api_version=st.secrets["VERSION_AZURE"],
    )
    api_kwargs["stream"] = True

elif config.API == "mistral":
    client = Mistral(api_key=st.secrets["KEY_MISTRAL"])
    api_kwargs["stream"] = True

else:
    raise ValueError(f"Unknown API: {config.API}")

# Check if voice input is enabled
if config.INPUT_MODE not in ["text", "voice", "text_and_voice"]:
    raise ValueError(
        "Please set INPUT_MODE to 'text', 'voice', or 'text_and_voice' in config.py."
    )
if "voice" in config.INPUT_MODE:
    if config.API == "openai":
        client_audio = OpenAI(
            api_key=st.secrets["KEY_OPENAI"],
        )
    elif config.API == "mistral":
        client_audio = Mistral(
            api_key=st.secrets["KEY_MISTRAL"],
        )


#
# 2. Display first message or previous conversation
#

# Define a container for the chat history
transcript_container = st.container()

# In case the interview history is still empty, pass system prompt to model and
# generate and display the first message
if not st.session_state.messages and st.session_state.interview_active:
    with transcript_container:
        with st.chat_message("assistant", avatar=config.AVATAR_INTERVIEWER):
            message_placeholder = st.empty()
            message_interviewer = stream_response(
                client, api_kwargs, message_placeholder
            )

    st.session_state.messages.append(
        {"role": "assistant", "content": message_interviewer}
    )
    save_backup(
        backups_directory=config.BACKUPS_DIRECTORY, admin_alias=config.ADMIN_ALIAS
    )
    st.rerun()

# Otherwise, display the previous conversation
else:
    with transcript_container:
        # Don't display system messages
        for message in st.session_state.messages:
            if message["role"] == "assistant":
                avatar = config.AVATAR_INTERVIEWER
            elif message["role"] == "user":
                avatar = config.AVATAR_RESPONDENT
            else:
                continue

            # Only display messages without codes
            if not any(code in message["content"] for code in config.CLOSING_MESSAGES):
                with st.chat_message(message["role"], avatar=avatar):
                    st.markdown(message["content"])


#
# 3. Main chat if interview is active
#

if st.session_state.interview_active:
    # Container for the chat and voice input elements used by the respondent
    response_container = st.container()

    with response_container:
        # Divider between chat and inputs
        st.divider()

        # Written message input for respondent
        text_input_element = st.empty()
        if "text" in config.INPUT_MODE:
            text_response = text_input_element.chat_input(
                config.TEXT_INPUT_INSTRUCTIONS
            )
        else:
            text_response = None

        # Alternative voice input
        voice_input_element = st.empty()
        if "voice" in config.INPUT_MODE:
            voice_response = voice_input_element.audio_input(
                config.VOICE_INPUT_INSTRUCTIONS,
                key=st.session_state.voice_input_key,
            )
            if config.API == "mistral" and voice_response is not None:
                voice_response={
                    "content": voice_response.getvalue(),
                    "file_name": voice_response.name,
                }

        else:
            voice_response = None

    # If respondent uses written input
    if text_response:
        message_respondent = text_response

        # Increase statistic for number of text answers given by respondent
        st.session_state.num_text_and_voice_answers[0] += 1

        with transcript_container:
            with st.chat_message("user", avatar=config.AVATAR_RESPONDENT):
                st.markdown(message_respondent)

    # If respondent uses voice input
    elif voice_response:
        # Do not display chat and voice inputs anymore for now
        text_input_element.empty()
        voice_input_element.empty()

        # Until message accepted
        message_respondent = None

        with transcript_container:
            # Show respondent transcription
            with st.chat_message("user", avatar=config.AVATAR_RESPONDENT):
                response_transcription_placeholder = st.empty()
                if not st.session_state.transcription_done:
                    response_transcription_placeholder.markdown(
                        "_Processing voice input ..._"
                    )
                    if config.API == "openai":
                        transcription_text = client_audio.audio.transcriptions.create(
                                model=config.MODEL_TRANSCRIPTION,
                                file=voice_response,
                            ).text
                    elif config.API == "mistral":
                        transcription_text =client_audio.audio.transcriptions.complete(
                                    model=config.MODEL_TRANSCRIPTION,
                                    file=voice_response,
                                ).text
                    st.session_state.response_transcription = (transcription_text)

                    response_transcription_placeholder.markdown(
                        st.session_state.response_transcription
                    )
                    st.session_state.transcription_done = True
                else:
                    response_transcription_placeholder.markdown(
                        st.session_state.response_transcription
                    )

            # Ask them to accept or reject
            col_accept, col_reject = st.columns([1, 1])

            accept_button_placeholder = col_accept.empty()
            reject_button_placeholder = col_reject.empty()

            # Now create the buttons inside those placeholders
            accept_clicked = accept_button_placeholder.button(
                "Proceed with this transcription."
            )
            reject_clicked = reject_button_placeholder.button("Enter a new answer.")

            if reject_clicked or accept_clicked:
                st.session_state.transcription_done = False
                accept_button_placeholder.empty()
                reject_button_placeholder.empty()

            # If user rejects, reset voice input and rerun
            if reject_clicked:
                st.session_state.voice_input_key = random.uniform(0, 1)
                st.rerun()

            # If user accepts, then proceed with the chat API call
            if accept_clicked:
                message_respondent = st.session_state.response_transcription

                # Increase statistic for number of voice answers given by respondent
                st.session_state.num_text_and_voice_answers[1] += 1

                with response_container:
                    st.session_state.voice_input_key = random.uniform(0, 1)
                    chat_key = random.uniform(0, 1)

                    # Written message input for respondent
                    if "text" in config.INPUT_MODE:
                        text_response = text_input_element.chat_input(
                            config.TEXT_INPUT_INSTRUCTIONS,
                            key=chat_key,
                        )
                    else:
                        text_response = None

                    # Alternative voice input
                    if "voice" in config.INPUT_MODE:
                        voice_response = voice_input_element.audio_input(
                            config.VOICE_INPUT_INSTRUCTIONS,
                            key=st.session_state.voice_input_key,
                        )
                    else:
                        voice_response = None

    # If neither voice nor text input given so far, set message to None
    else:
        message_respondent = None

    # Once the user response has been given
    if message_respondent:
        # Append message
        st.session_state.messages.append(
            {"role": "user", "content": message_respondent}
        )

        with transcript_container:
            # Generate and display interviewer response to message
            with st.chat_message("assistant", avatar=config.AVATAR_INTERVIEWER):
                # Create placeholder for message in chat interface
                message_placeholder = st.empty()

                # Stream response from chat API
                message_interviewer = stream_response(
                    client=client,
                    client_kwargs=api_kwargs,
                    message_placeholder=message_placeholder,
                )

                # Display closing message if code is in the message and end interview
                for code in config.CLOSING_MESSAGES.keys():
                    if code in message_interviewer:
                        # Set flag
                        st.session_state.interview_active = False

                        # Display closing message
                        closing_message = config.CLOSING_MESSAGES[code]
                        st.markdown(closing_message)

                        st.session_state.messages.append(
                            {
                                "role": "assistant",
                                "content": message_interviewer,  # original message
                            }
                        )
                        st.session_state.messages.append(
                            {
                                "role": "assistant",
                                "content": closing_message,  # displayed message
                            }
                        )

                        # Store final transcript and backup
                        save_transcript_and_metadata(
                            transcripts_directory=config.TRANSCRIPTS_DIRECTORY,
                            metadata_directory=config.METADATA_DIRECTORY,
                            api_kwargs=api_kwargs,
                            admin_alias=config.ADMIN_ALIAS,
                        )
                        save_backup(
                            backups_directory=config.BACKUPS_DIRECTORY,
                            admin_alias=config.ADMIN_ALIAS,
                        )

                        # Clear chat and voice input elements upon completion of the
                        # interview (seems slightly faster than relying only on rerun)
                        text_input_element.empty()
                        voice_input_element.empty()
                        st.rerun()

                # Otherwise (i.e. if no code in message) arrive here

                # Append message
                st.session_state.messages.append(
                    {"role": "assistant", "content": message_interviewer}
                )

                # Attempt a backup save but continue interview if writing fails
                try:
                    save_backup(
                        backups_directory=config.BACKUPS_DIRECTORY,
                        admin_alias=config.ADMIN_ALIAS,
                    )
                except Exception:
                    pass

        # Refresh to show a new voice_input element
        st.session_state.voice_input_key = random.uniform(0, 1)
        st.rerun()
