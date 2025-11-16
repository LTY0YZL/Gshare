// Check if SpeechRecognition is supported
let latestTranscript = '';

let voiceMessages = [];
let voiceSessionActive = false;
function saveVoiceMessages() {
  localStorage.setItem('voiceMessages', JSON.stringify(voiceMessages));
}
function loadVoiceMessages() {
  const raw = localStorage.getItem('voiceMessages');
  if (!raw) return;
  const parsed = JSON.parse(raw);
  if (Array.isArray(parsed)) {
    voiceMessages = parsed;
  }
}
loadVoiceMessages();


function getCsrfToken() {
  const meta = document.querySelector('meta[name="csrf-token"]');
  return meta ? meta.content : '';
}

function renderVoiceChat() {
  const container = document.getElementById('speech-output');
  if (!container) return;

  let html = '';
  for (const m of voiceMessages) {
    const isUser = m.role === 'user';
    const alignmentStyle = isUser ? 'text-align:right;' : 'text-align:left;';
    const bubbleStyle = isUser
      ? 'display:inline-block; padding:4px 8px; margin:4px 0; border-radius:6px; background:#dbeafe; color:#111827; white-space:pre-wrap;'
      : 'display:inline-block; padding:4px 8px; margin:4px 0; border-radius:6px; background:#f3f4f6; color:#111827; white-space:pre-wrap;';
    const text = m.content || '';
    html += '<div style="' + alignmentStyle + '"><div style="' + bubbleStyle + '">' + text + '</div></div>';
  }
  container.innerHTML = html;
  container.scrollTop = container.scrollHeight;
}

window.startVoiceOrderSession = function() {
  voiceSessionActive = true;
  if (!voiceMessages.length) {
    voiceMessages = [{
      role: 'assistant',
      content: 'Tell me what you want to buy from [store name], including quantities and brands you prefer.'
    }];
  }
  const startContainer = document.getElementById('voice-order-start-container');
  if (startContainer) {
    startContainer.style.display = 'none';
  }

  const controls = document.getElementById('voice-controls');
  if (controls) {
    controls.classList.remove('hidden');
    controls.style.display = 'flex';
  }
  renderVoiceChat();
  saveVoiceMessages();
};

window.restartVoiceOrderSession = function() {
  voiceMessages = [];
  localStorage.removeItem('voiceMessages');
  startVoiceOrderSession();
};

window.sendVoiceChatMessage = function() {
  if (!latestTranscript) {
    return;
  }

  // Add the latest user utterance to the conversation history
  voiceMessages.push({ role: 'user', content: latestTranscript });
  const sendingMessages = voiceMessages.slice();

  // Optional: simple loading state by adding a temporary assistant message
  voiceMessages.push({ role: 'assistant', content: 'Thinking...' });
  renderVoiceChat();

  const csrfToken = getCsrfToken();

  fetch('/shoppingcart/voice_order/chat/', {
    method: 'POST',
    credentials: 'same-origin',
    headers: {
      'Content-Type': 'application/json',
      'X-CSRFToken': csrfToken || ''
    },
    body: JSON.stringify({ messages: sendingMessages })
  })
    .then(resp => {
      if (!resp.ok) {
        throw new Error('Chat request failed with status ' + resp.status);
      }
      return resp.json();
    })
    .then(data => {
      const assistantText = data.assistant || 'Sorry, I could not generate a response.';
      voiceMessages[voiceMessages.length - 1] = { role: 'assistant', content: assistantText };
      renderVoiceChat();
    })
    .catch(err => {
      console.error('Chat error:', err);
      voiceMessages[voiceMessages.length - 1] = {
        role: 'assistant',
        content: 'Sorry, I encountered an error. Please try again.'
      };
      renderVoiceChat();
    })
    .finally(() => {
      // Clear the latest transcript so we don't re-send it accidentally
      latestTranscript = '';
      saveVoiceMessages();
    });
};

window.sendTypedVoiceChatMessage = function() {
  const input = document.getElementById('voice-text-input');
  if (!input) {
    return;
  }
  const text = input.value.trim();
  if (!text) {
    return;
  }
  latestTranscript = text;
  input.value = '';
  window.sendVoiceChatMessage();
};


window.finalizeVoiceOrderCart = function() {
  if (!voiceMessages.length) {
    return;
  }

  voiceMessages.push({ role: 'assistant', content: 'Adding these items to your cart...' });
  renderVoiceChat();
  saveVoiceMessages();

  const csrfToken = getCsrfToken();

  fetch('/shoppingcart/voice_order/chat/', {
    method: 'POST',
    credentials: 'same-origin',
    headers: {
      'Content-Type': 'application/json',
      'X-CSRFToken': csrfToken || ''
    },
    body: JSON.stringify({ messages: voiceMessages, mode: 'finalize' })
  })
    .then(resp => resp.json())
    .then(data => {
      if (!data.success || !data.cart) {
        const msg = data.error || 'Could not finalize cart.';
        voiceMessages.push({ role: 'assistant', content: msg });
        return;
      }

      // const pretty = JSON.stringify(data.cart, null, 2);
      // voiceMessages.push({ role: 'assistant', content: 'Final cart JSON:\n' + pretty });

      if (data.order_id) {
        voiceMessages.push({
          role: 'assistant',
          content: 'I added these items to your <a href="/shoppingcart/" style="color:#2563eb; text-decoration:underline;">cart</a>.'
        });
      }
    })
    .catch(err => {
      console.error('Finalize error:', err);
      voiceMessages.push({ role: 'assistant', content: 'Sorry, I could not finalize the cart.' });
    })
    .finally(() => {
      renderVoiceChat();
      saveVoiceMessages();
    });
}



if ('webkitSpeechRecognition' in window || 'SpeechRecognition' in window) {
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    const recognition = new SpeechRecognition();

    // Configure recognition settings
    recognition.continuous = false; // Stop after one phrase
    recognition.interimResults = false; // Only final results
    recognition.lang = 'en-US'; // Language

    // Event handler for when speech is recognized
    recognition.onresult = (event) => {
        const transcript = event.results[0][0].transcript;
        latestTranscript = transcript.trim();

        if (voiceSessionActive && typeof window.sendVoiceChatMessage === 'function') {
            window.sendVoiceChatMessage();
        } else {
            const output = document.getElementById('speech-output');
            if (output) {
                output.textContent = 'You said: ' + transcript;
            }
        }
    };

    // Event handler for when recognition ends
    recognition.onend = () => {};

    // Event handler for errors
    recognition.onerror = (event) => {
        console.error('Speech recognition error: ' + event.error);
    };

    // Function to start recognition (call this from HTML button)
    window.startSpeechToText = () => {
        recognition.start();
    };

} else {
    console.error('Speech Recognition not supported in this browser');
}
