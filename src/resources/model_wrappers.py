from abc import ABC, abstractmethod
from typing import List, TypedDict, Optional
from openai import OpenAI, APIError
from anthropic import Anthropic, APIConnectionError, RateLimitError, APIStatusError
import time
import logging
import os
from tqdm import tqdm
import vertexai
from vertexai.generative_models import GenerativeModel, Part, Content, GenerationConfig
import google.api_core.exceptions

logger = logging.getLogger(__name__)
logger.setLevel(logging.WARNING)

class Message(TypedDict):
    role: str
    content: str

class ModelWrapper(ABC):
    """Abstract base class for model API wrappers."""
    
    def __init__(
        self,
        model_name: str,
        temperature: float = 0.7,
        max_tokens: int = 4000,
        top_p: float = 1.0,
        max_retries: int = 3,
        initial_retry_delay: float = 1.0,
        **kwargs
    ):
        """
        Initialize the model wrapper.
        
        Args:
            model_name: Name of the model to use
            temperature: Sampling temperature (0-1)
            max_tokens: Maximum number of tokens to generate
            top_p: Top-p sampling parameter
            max_retries: Maximum number of retry attempts
            initial_retry_delay: Initial delay between retries (seconds)
            **kwargs: Additional model-specific parameters
        """
        self.model_name = model_name
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.top_p = top_p
        self.max_retries = max_retries
        self.initial_retry_delay = initial_retry_delay
        self.additional_params = kwargs

    @abstractmethod
    def generate(self, messages: List[Message]) -> str:
        """Generate a response for a single prompt."""
        pass

    @abstractmethod
    def batch_generate(self, messages_list: List[List[Message]]) -> List[str]:
        """Generate responses for multiple prompts."""
        pass

    @classmethod
    def create(cls, model_name: str, **kwargs) -> 'ModelWrapper':
        """Factory method to create appropriate model wrapper instance."""
        if "gpt" in model_name.lower():
            return OpenAIClient(model_name, **kwargs)
        elif "claude" in model_name.lower():
            return AnthropicClient(model_name, **kwargs)
        elif "gemini" in model_name.lower():
            return GoogleClient(model_name, **kwargs)
        else:
            return OpenRouterClient(model_name, **kwargs)

    def _exponential_backoff(self, attempt: int) -> None:
        """Implement exponential backoff between retries."""
        if attempt < self.max_retries:
            delay = self.initial_retry_delay * (2 ** attempt)
            time.sleep(delay)

class OpenAIClient(ModelWrapper):
    
    def __init__(self, model_name: str, **kwargs):
        super().__init__(model_name, **kwargs)
        self.client = OpenAI(timeout=60)
    
    def generate(self, messages: List[Message]) -> str:
        for attempt in range(self.max_retries):
            try:
                response = self.client.chat.completions.create(
                    model=self.model_name,
                    messages=messages,
                    temperature=self.temperature,
                    max_completion_tokens=self.max_tokens,
                    top_p=self.top_p,
                    **self.additional_params
                )
                return response.choices[0].message.content
            except APIError as e:
                logger.warning(f"OpenAI API error (attempt {attempt + 1}/{self.max_retries}): {str(e)} for prompt {messages}")
                if attempt == self.max_retries - 1:
                    logger.error(f"Failed after {self.max_retries} attempts: {str(e)}")
                    return None
                self._exponential_backoff(attempt)

    def batch_generate(self, messages_list: List[List[Message]]) -> List[str]:
         #TODO: implement batch API for message_lists that are sufficiently long
        responses = []
        for messages in tqdm(messages_list, desc='Batch Generation'):
            responses.append(self.generate(messages))
        return responses

class OpenRouterClient(ModelWrapper):

    def __init__(self, model_name: str, **kwargs):
        super().__init__(model_name, **kwargs)
        self.client = OpenAI(
            base_url = "https://openrouter.ai/api/v1",
            api_key = os.getenv("OPENROUTER_API_KEY")
        )
    
    def generate(self, messages: List[Message]) -> str:
        for attempt in range(self.max_retries):
            try:
                response = self.client.chat.completions.create(
                    model=self.model_name,
                    messages=messages,
                    temperature=self.temperature,
                    max_completion_tokens=self.max_tokens,
                    top_p=self.top_p,
                    **self.additional_params
                )
                return response.choices[0].message.content
            except APIError as e:
                logger.warning(f"OpenRouter API error (attempt {attempt + 1}/{self.max_retries}): {str(e)} for prompt {messages}")
                if attempt == self.max_retries - 1:
                    logger.error(f"Failed after {self.max_retries} attempts: {str(e)}")
                    return None
                self._exponential_backoff(attempt)

    def batch_generate(self, messages_list: List[List[Message]]) -> List[str]:
         #TODO: implement batch API for message_lists that are sufficiently long
        responses = []
        for messages in tqdm(messages_list, desc='Batch Generation'):
            responses.append(self.generate(messages))
        return responses


class AnthropicClient(ModelWrapper):
    
    def __init__(self, model_name: str, **kwargs):
        super().__init__(model_name, **kwargs)
        self.client = Anthropic(timeout=60)
    
    def generate(self, messages: List[Message]) -> str:

        if messages[0]['role'] == 'system':
            system = messages[0]['content']
            messages = messages[1:]
        else:
            system = 'You are a helpful assistant.'

        for attempt in range(self.max_retries):
            try:
                # temperature/top_p are not sent: Claude 5-family models (e.g. claude-haiku-5-5) reject
                # sampling parameters with a 400 "`temperature` is deprecated for this model". The
                # --temperature CLI flag therefore has no effect on Claude models. (Claude, Oct 2026)
                response = self.client.messages.create(
                    model=self.model_name,
                    system=system,
                    messages=messages,
                    max_tokens=self.max_tokens,
                    **self.additional_params
                )
                # Thinking is on by default on Claude 5-family models, so content[0] may be a
                # thinking block; return the first text block instead. (Claude, Oct 2026)
                for block in response.content:
                    if getattr(block, "type", None) == "text":
                        return block.text
                logger.error(f"Anthropic response contained no text block: {response.content}")
                return None

            except (APIConnectionError, RateLimitError, APIStatusError) as e:
                logger.warning(f"Anthropic API error (attempt {attempt + 1}/{self.max_retries}): {str(e)}")
                if attempt == self.max_retries - 1:
                    logger.error(f"Failed after {self.max_retries} attempts: {str(e)} for prompt {messages}")
                    return None
                self._exponential_backoff(attempt)

    def batch_generate(self, messages_list: List[List[Message]]) -> List[str]:
        #TODO: implement batch API for message_lists that are sufficiently long
        responses = []
        for messages in tqdm(messages_list, desc='Batch Generation'):
            responses.append(self.generate(messages))
        return responses


class GoogleClient(ModelWrapper):

    def __init__(
        self,
        model_name: str,
        project: Optional[str] = os.getenv("GOOGLE_PROJECT_ID"),
        location: str = "us-central1",
        **kwargs
    ):
        super().__init__(model_name, **kwargs)
        if project is None:
            raise ValueError("Google Project ID must be provided via argument or GOOGLE_PROJECT_ID env variable.")
        vertexai.init(project=project, location=location)
        print(f"project is {project}")
        self.model = GenerativeModel(model_name=self.model_name)

    def _convert_messages_to_contents(self, messages: List[Message]) -> List[Content]:
        """Converts OpenAI/Anthropic style messages to Vertex AI Content objects."""
        contents = []
        for message in messages:
            role = message['role']
            if role == 'assistant':
                role = 'model'
            if role in ['user', 'model']:
                contents.append(Content(role=role, parts=[Part.from_text(message['content'])]))
        return contents

    def generate(self, messages: List[Message]) -> str:
        system_prompt = None
        if messages and messages[0]['role'] == 'system':
            system_prompt = messages[0]['content']
            messages = messages[1:]

        contents = self._convert_messages_to_contents(messages)

        generation_config = GenerationConfig(
            temperature=self.temperature,
            # max_output_tokens=self.max_tokens,
            max_output_tokens=65535,
            top_p=self.top_p,
        )

        for attempt in range(self.max_retries):
            try:
                response = self.model.generate_content(
                    contents=contents,
                    generation_config=generation_config,
                )
                if response.candidates and response.candidates[0].content.parts:
                     return response.candidates[0].content.parts[0].text
                else:
                    logger.warning(f"Google API returned no content (attempt {attempt + 1}/{self.max_retries}) for prompt {messages}. Finish reason: {response.candidates[0].finish_reason if response.candidates else 'N/A'}")
                    return None

            except (google.api_core.exceptions.RetryError,
                    google.api_core.exceptions.ServiceUnavailable,
                    google.api_core.exceptions.InternalServerError,
                    google.api_core.exceptions.ResourceExhausted,
                    google.api_core.exceptions.InvalidArgument) as e:
                logger.warning(f"Google API error (attempt {attempt + 1}/{self.max_retries}): {str(e)} for prompt {messages}")
                if attempt == self.max_retries - 1:
                    logger.error(f"Failed after {self.max_retries} attempts: {str(e)}")
                    return None
                self._exponential_backoff(attempt)
            except Exception as e:
                logger.error(f"Unexpected error during Google API call: {str(e)} for prompt {messages}")
                return None

    def batch_generate(self, messages_list: List[List[Message]]) -> List[str]:
        responses = []
        for messages in tqdm(messages_list, desc='Google Batch Generation'):
            responses.append(self.generate(messages))
        return responses
