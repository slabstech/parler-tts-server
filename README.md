# Parler-TTS-Server
This repository provides a server with an [OpenAI compatible API](https://platform.openai.com/docs/api-reference/audio/createSpeech) interface for [Parler-TTS](https://github.com/huggingface/parler-tts).

- Download model weights from huggingface
  - huggingface-cli download ai4bharat/indic-parler-tts

## Quick Start 
Docker
```bash
docker run --detach --volume ~/.cache/huggingface:/root/.cache/huggingface --publish 8000:8000 slabstech/parler-tts-server
```
Using a fine-tuned model. See [main.py](./parler_tts_server/main.py) for configurable options
```bash
docker run --detach --volume ~/.cache/huggingface:/root/.cache/huggingface --publish 8000:8000 --env MODEL="ai4bharat/indic-parler-tts" slabstech/parler-tts-server
```
Docker Compose
```bash
curl -sO https://raw.githubusercontent.com/sachinsshetty/parler-tts-server/refs/heads/master/compose.yaml
docker compose up --detach parler-tts-server
```

## Usage 

- kannada
  - curl -s -H "content-type: application/json" localhost:8000/v1/audio/speech -d '{"input": "ಉದ್ಯಾನದಲ್ಲಿ ಮಕ್ಕಳ ಆಟವಾಡುತ್ತಿದ್ದಾರೆ ಮತ್ತು ಪಕ್ಷಿಗಳು ಚಿಲಿಪಿಲಿ ಮಾಡುತ್ತಿವೆ."}' -o audio.mp3
    
- hindi
    -  curl -s -H "content-type: application/json" localhost:8000/v1/audio/speech -d '{"input": "अरे, तुम आज कैसे हो?"}' -o audio.mp3


Saving to file
```bash
curl -s -H "content-type: application/json" localhost:8000/v1/audio/speech -d '{"input": "Hey, how are you?"}' -o audio.mp3
```
Specifying a different format.
```bash
curl -s -H "content-type: application/json" localhost:8000/v1/audio/speech -d '{"input": "Hey, how are you?", "response_type": "wav"}' -o audio.wav
```
Playing back the audio
```bash
curl -s -H "content-type: application/json" localhost:8000/v1/audio/speech -d '{"input": "Hey, how are you?"}' | ffplay -hide_banner -autoexit -nodisp -loglevel quiet -
```
Describing the voice the model should output
```bash
curl -s -H "content-type: application/json" localhost:8000/v1/audio/speech -d '{"input": "Hey, how are you?", "voice": "Feminine, speedy, and cheerfull"}' | ffplay -hide_banner -autoexit -nodisp -loglevel quiet -
```

OpenAI SDK usage example can be found [here](./examples/openai_sdk.py)

## Citations
```
@misc{lacombe-etal-2024-parler-tts,
  author = {Yoach Lacombe and Vaibhav Srivastav and Sanchit Gandhi},
  title = {Parler-TTS},
  year = {2024},
  publisher = {GitHub},
  journal = {GitHub repository},
  howpublished = {\url{https://github.com/huggingface/parler-tts}}
}
```
```
@misc{lyth2024natural,
      title={Natural language guidance of high-fidelity text-to-speech with synthetic annotations},
      author={Dan Lyth and Simon King},
      year={2024},
      eprint={2402.01912},
      archivePrefix={arXiv},
      primaryClass={cs.SD}
}
```
