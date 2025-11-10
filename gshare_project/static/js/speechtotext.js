// Check if SpeechRecognition is supported
let latestTranscript = '';

function getCsrfToken() {
  const meta = document.querySelector('meta[name="csrf-token"]');
  if (meta && meta.content) return meta.content;
}

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
        //console.log('You said: ' + transcript);
        // If a display element exists on the page, update it
        const output = document.getElementById('speech-output');
        if (output) {
            output.textContent = 'You said: ' + transcript;
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
} else {
    console.error('Speech Recognition not supported in this browser');
}
