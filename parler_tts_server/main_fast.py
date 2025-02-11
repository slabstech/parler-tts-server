import time
from contextlib import asynccontextmanager
from typing import Annotated, Any, OrderedDict,List
import zipfile
import os
import huggingface_hub
import soundfile as sf
import torch
from fastapi import Body, FastAPI, HTTPException, Response, BackgroundTasks
from fastapi.responses import FileResponse
from huggingface_hub.hf_api import ModelInfo
from openai.types import Model
from parler_tts import ParlerTTSForConditionalGeneration
from transformers import AutoTokenizer, AutoFeatureExtractor, set_seed

from parler_tts_server.config import SPEED, ResponseFormat, config
from parler_tts_server.logger import logger

# https://github.com/huggingface/parler-tts?tab=readme-ov-file#usage
if torch.cuda.is_available():
    device = "cuda:0"
    logger.info("GPU will be used for inference")
else:
    device = "cpu"
    logger.info("CPU will be used for inference")
torch_dtype = torch.float16 if device != "cpu" else torch.float32

# Check CUDA availability and version
cuda_available = torch.cuda.is_available()
cuda_version = torch.version.cuda if cuda_available else None


if torch.cuda.is_available():
    device = torch.cuda.current_device()
    capability = torch.cuda.get_device_capability(device)
    compute_capability_float = float(f"{capability[0]}.{capability[1]}")
    print(f"CUDA version: {cuda_version}")

    print(f"CUDA Compute Capability: {compute_capability_float}")
else:
    print("CUDA is not available on this system.")


class ModelManager:
    def __init__(self):
        self.model_tokenizer: OrderedDict[
            str, tuple[ParlerTTSForConditionalGeneration, AutoTokenizer]
        ] = OrderedDict()

    def load_model(
        self, model_name: str
    ) -> tuple[ParlerTTSForConditionalGeneration, AutoTokenizer]:
        logger.debug(f"Loading {model_name}...")
        start = time.perf_counter()
        model = ParlerTTSForConditionalGeneration.from_pretrained(model_name).to(  # type: ignore
            device,  # type: ignore
            dtype=torch_dtype,
        )
        tokenizer = AutoTokenizer.from_pretrained(model_name)
        description_tokenizer = AutoTokenizer.from_pretrained(model.config.text_encoder._name_or_path)

        
        if cuda_available and compute_capability_float > 7.1:  # check for triton compiler
            # compile the forward pass
            #compile_mode = "default" # chose "reduce-overhead" for 3 to 4x speed-up
            compile_mode = "reduce-overhead"
            model.generation_config.cache_implementation = "static"
            model.forward = torch.compile(model.forward, mode=compile_mode)


            # need to set padding max length
            max_length = 50
            torch_device = "cuda:0"

                    # warmup
            inputs = tokenizer("This is for compilation", return_tensors="pt", padding="max_length", max_length=max_length).to(torch_device)

            model_kwargs = {**inputs, "prompt_input_ids": inputs.input_ids, "prompt_attention_mask": inputs.attention_mask, }

            n_steps = 1 if compile_mode == "default" else 2
            for _ in range(n_steps):
                _ = model.generate(**model_kwargs)
        
        logger.info(
            f"Loaded {model_name} and tokenizer in {time.perf_counter() - start:.2f} seconds"
        )
        return model, tokenizer, description_tokenizer

    def get_or_load_model(
        self, model_name: str
    ) -> tuple[ParlerTTSForConditionalGeneration, Any]:
        if model_name not in self.model_tokenizer:
            logger.info(f"Model {model_name} isn't already loaded")
            if len(self.model_tokenizer) == config.max_models:
                logger.info("Unloading the oldest loaded model")
                del self.model_tokenizer[next(iter(self.model_tokenizer))]
            self.model_tokenizer[model_name] = self.load_model(model_name)
        return self.model_tokenizer[model_name]


model_manager = ModelManager()


@asynccontextmanager
async def lifespan(_: FastAPI):
    if not config.lazy_load_model:
        model_manager.get_or_load_model(config.model)
    yield


app = FastAPI(lifespan=lifespan)


@app.get("/health")
def health() -> Response:
    return Response(status_code=200, content="OK")


@app.get("/v1/models", response_model=list[Model])
def get_models() -> list[Model]:
    models = list(huggingface_hub.list_models(model_name="parler-tts"))
    models = [
        Model(
            id=model.id,
            created=int(model.created_at.timestamp()),
            object="model",
            owned_by=model.id.split("/")[0],
        )
        for model in models
        if model.created_at is not None
    ]
    return models


@app.get("/v1/models/{model_name:path}", response_model=Model)
def get_model(model_name: str) -> Model:
    models = list(huggingface_hub.list_models(model_name=model_name))
    if len(models) == 0:
        raise HTTPException(status_code=404, detail="Model doesn't exists")
    exact_match: ModelInfo | None = None
    for model in models:
        if model.id == model_name:
            exact_match = model
            break
    if exact_match is None:
        raise HTTPException(
            status_code=404,
            detail=f"Model doesn't exists. Possible matches: {", ".join([model.id for model in models])}",
        )
    assert exact_match.created_at is not None
    return Model(
        id=exact_match.id,
        created=int(exact_match.created_at.timestamp()),
        object="model",
        owned_by=exact_match.id.split("/")[0],
    )


# https://platform.openai.com/docs/api-reference/audio/createSpeech
@app.post("/v1/audio/speech")
async def generate_audio(
    input: Annotated[str, Body()],
    voice: Annotated[str, Body()] = config.voice,
    model: Annotated[str, Body()] = config.model,
    response_format: Annotated[ResponseFormat, Body()] = config.response_format,
    speed: Annotated[float, Body()] = SPEED,
) -> FileResponse:
    tts, tokenizer,description_tokenizer = model_manager.get_or_load_model(model)
    if speed != SPEED:
        logger.warning(
            "Specifying speed isn't supported by this model. Audio will be generated with the default speed"
        )
    start = time.perf_counter()
    #input_ids = tokenizer(voice, return_tensors="pt").input_ids.to(device)
    input_ids = description_tokenizer(voice, return_tensors="pt").input_ids.to(device)
    
    prompt_input_ids = tokenizer(input, return_tensors="pt").input_ids.to(device)
    generation = tts.generate(
        input_ids=input_ids, prompt_input_ids=prompt_input_ids
    ).to(  # type: ignore
        torch.float32
    )
    audio_arr = generation.cpu().numpy().squeeze()
    logger.info(
        f"Took {time.perf_counter() - start:.2f} seconds to generate audio for {len(input.split())} words using {device.upper()}"
    )
    # TODO: use an in-memory file instead of writing to disk
    sf.write(f"out.{response_format}", audio_arr, tts.config.sampling_rate)
    return FileResponse(f"out.{response_format}", media_type=f"audio/{response_format}")

def zip_files(file_paths, zip_filename):
    with zipfile.ZipFile(zip_filename, 'w') as zipf:
        for file_path in file_paths:
            zipf.write(file_path, os.path.basename(file_path))

def cleanup_files(file_paths, zip_filename):
    for file_path in file_paths:
        os.remove(file_path)
    os.remove(zip_filename)

# https://platform.openai.com/docs/api-reference/audio/createSpeech
@app.post("/v1/audio/speech_batch")
async def generate_audio_batch(
    input: Annotated[List[str], Body()],
    voice: Annotated[List[str], Body()] = config.voice,
    model: Annotated[str, Body()] = config.model,
    response_format: Annotated[ResponseFormat, Body()] = config.response_format,
    speed: Annotated[float, Body()] = SPEED,
) -> FileResponse:
    tts, tokenizer,description_tokenizer = model_manager.get_or_load_model(model)
    if speed != SPEED:
        logger.warning(
            "Specifying speed isn't supported by this model. Audio will be generated with the default speed"
        )
    start = time.perf_counter()
    #input_ids = tokenizer(voice, return_tensors="pt").input_ids.to(device)
    inputs = description_tokenizer(voice, return_tensors="pt", padding=True).to(device)

    prompt = tokenizer(input, return_tensors="pt", padding=True).to(device)

    set_seed(0)
    generation = tts.generate(
        input_ids=inputs.input_ids,
        attention_mask=inputs.attention_mask,
        prompt_input_ids=prompt.input_ids,
        prompt_attention_mask=prompt.attention_mask,
        do_sample=True,
        return_dict_in_generate=True,
    )

    # Create a list to hold the paths of the files to be zipped
    file_paths = []

    for i, audio in enumerate(generation.sequences):
        audio_arr = audio[:generation.audios_length[i]].cpu().numpy().squeeze()
        audio_arr = audio_arr.astype('float32')
        file_path = f"out_{i}.{response_format}"
        sf.write(file_path, audio_arr, tts.config.sampling_rate)
        file_paths.append(file_path)

    # Zip the files
    zip_filename = "audio_files.zip"
    zip_files(file_paths, zip_filename)

    # Cleanup the temporary files
    for file_path in file_paths:
        os.remove(file_path)

    # Return the zip file
    
      # Register the cleanup task
    #background_tasks.add_task(cleanup_files, file_paths, zip_filename)

    ''' TODO - fix conversion
    logger.info(
        f"Took {time.perf_counter() - start:.2f} seconds to generate audio for {len(input_str.split())} words using {device.upper()}"
    )
    '''
    logger.info(
        f"Took {time.perf_counter() - start:.2f} seconds to generate audio"
    )
    # TODO: use an in-memory file instead of writing to disk
    #sf.write(f"out.{response_format}", audio_arr, tts.config.sampling_rate)
    #return FileResponse(f"out.{response_format}", media_type=f"audio/{response_format}")
    return FileResponse(zip_filename, media_type="application/zip")
