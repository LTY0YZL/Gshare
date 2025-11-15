// Check if SpeechRecognition is supported
let latestTranscript = '';
let AIsResponse = '';

let voiceMessages = [];
let voiceSessionActive = false;

function getCsrfToken() {
  const meta = document.querySelector('meta[name="csrf-token"]');
  if (meta && meta.content) return meta.content;
}

function renderVoiceChat() {
  const container = document.getElementById('speech-output');
  if (!container){
    console.warn('renderVoiceChat(): speech-output element not found');
    return;
  }

  let html = '';
  for (const m of voiceMessages) {
    const isUser = m.role === 'user';
    const alignmentStyle = isUser ? 'text-align:right;' : 'text-align:left;';
    const bubbleStyle = isUser
      ? 'display:inline-block; padding:4px 8px; margin:4px 0; border-radius:6px; background:#dbeafe; color:#111827;'
      : 'display:inline-block; padding:4px 8px; margin:4px 0; border-radius:6px; background:#f3f4f6; color:#111827;';
    const text = m.content || '';
    html += '<div style="' + alignmentStyle + '"><div style="' + bubbleStyle + '">' + text + '</div></div>';
  }
  container.innerHTML = html;
  container.scrollTop = container.scrollHeight;
}

window.startVoiceOrderSession = function() {
  voiceSessionActive = true;
  voiceMessages = [{
    role: 'assistant',
    content: 'Please state your desired cart in the following format: From [store_name] I would like quantity -> itemName, quantity -> itemName. If possible, be specific about your desired brand for each product.'
  }];

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
};

window.sendVoiceChatMessage = function() {
  if (!voiceSessionActive || !latestTranscript) {
    console.warn('No active voice session or transcript to send.');
    return;
  }

  voiceMessages.push({ role: 'user', content: latestTranscript });
  voiceMessages.push({
    role: 'assistant',
    content: 'Thanks, I received that: ' + latestTranscript
  });
  renderVoiceChat();
};

function ConvertTranscript() {
    const transcript = latestTranscript;
    if (!transcript) {
        console.warn('No transcript available to send.');
        return;
    }

    const csrfToken = getCsrfToken();

    fetch('/shoppingcart/voice_order/process/', {
        method: 'POST',
        credentials: 'same-origin',
        headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': csrfToken
        },
        body: JSON.stringify({ transcript: transcript })
    }).then(resp => {
        console.log('Voice POST response status:', resp.status);
        if (!resp.ok) {
            console.error('Voice POST failed with status:', resp.status);
        }
        return resp.json().catch(()=>null);
    }).then(data => {
        if (data) {
            console.log('Voice response:', data);
        } else {
            console.warn('No data returned from voice endpoint');
        }
    }).catch(err => {
        console.error('Voice send error:', err);
    });
}

function createCart() {
    if (!AIsResponse) {
        console.warn('No order JSON available to send.');
        return;
    }

    const csrfToken = getCsrfToken();

    fetch('/shoppingcart/voice_order/process/', {
        method: 'POST',
        credentials: 'same-origin',
        headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': csrfToken
        },
        body: JSON.stringify({ transcript: transcript })
    }).then(resp => {
        console.log('Voice POST response status:', resp.status);
        if (!resp.ok) {
            console.error('Voice POST failed with status:', resp.status);
        }
        return resp.json().catch(()=>null);
    }).then(data => {
        if (data) {
            console.log('Voice response:', data);
        } else {
            console.warn('No data returned from voice endpoint');
        }
    }).catch(err => {
        console.error('Voice send error:', err);
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
    recognition.onend = () => {
        console.log('Speech recognition ended');
    };

    // Event handler for errors
    recognition.onerror = (event) => {
        console.error('Speech recognition error: ' + event.error);
    };

    // Function to start recognition (call this from HTML button)
    window.startSpeechToText = () => {
        recognition.start();
    };

    window.sendTranscript = () => {
        ConvertTranscript();
    }

    window.createCartFromTranscript = () => {
        createCart();
    }
} else {
    console.error('Speech Recognition not supported in this browser');
}
