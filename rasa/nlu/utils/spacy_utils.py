import typing
import logging
from typing import Any, Dict, List, Optional, Text, Tuple, Union

import rasa.shared.utils.io
import rasa.utils.train_utils
from rasa.nlu.components import Component
from rasa.nlu.model import InvalidModelError
from rasa.nlu.config import RasaNLUModelConfig
from rasa.shared.nlu.training_data.message import Message
from rasa.shared.nlu.training_data.training_data import TrainingData
from rasa.shared.constants import DOCS_URL_COMPONENTS
from rasa.nlu.constants import SPACY_DOCS, DENSE_FEATURIZABLE_ATTRIBUTES

logger = logging.getLogger(__name__)

if typing.TYPE_CHECKING:
    from spacy.language import Language
    from spacy.tokens.doc import Doc
    from rasa.nlu.model import Metadata


class SpacyNLP(Component):
    """The core component that links spaCy to related components in the pipeline."""

    defaults = {
        # name of the language model to load
        "model": None,
        # when retrieving word vectors, this will decide if the casing
        # of the word is relevant. E.g. `hello` and `Hello` will
        # retrieve the same vector, if set to `False`. For some
        # applications and models it makes sense to differentiate
        # between these two words, therefore setting this to `True`.
        "case_sensitive": False,
    }

    def __init__(
        self, component_config: Dict[Text, Any] = None, nlp: "Language" = None
    ) -> None:

        self.nlp = nlp
        super().__init__(component_config)

    @staticmethod
    def load_model(spacy_model_name: Text) -> "Language":
        """Try loading the model, catching the OSError if missing."""
        import spacy

        try:
            return spacy.load(spacy_model_name, disable=["parser"])
        except OSError:
            raise InvalidModelError(
                f"Please confirm that {spacy_model_name} is an available spaCy model. "
                f"You need to download one upfront. For example:\npython -m spacy download "
                f"en_core_web_md\n"
                f"More informaton can be found on {DOCS_URL_COMPONENTS}#spacynlp"
            )

    @classmethod
    def required_packages(cls) -> List[Text]:
        return ["spacy"]

    @classmethod
    def create(
        cls, component_config: Dict[Text, Any], config: RasaNLUModelConfig
    ) -> "SpacyNLP":

        component_config = rasa.utils.train_utils.override_defaults(
            cls.defaults, component_config
        )

        spacy_model_name = cls._check_model_fallback(
            component_config.get("model"), config.language, warn=True
        )

        logger.info(f"Trying to load spacy model with name '{spacy_model_name}'")

        nlp = cls.load_model(spacy_model_name)

        cls.ensure_proper_language_model(nlp)
        return cls(component_config, nlp)

    @classmethod
    def cache_key(
        cls, component_meta: Dict[Text, Any], model_metadata: "Metadata"
    ) -> Optional[Text]:

        spacy_model_name = cls._check_model_fallback(
            component_meta.get("model"), model_metadata.language, warn=False
        )

        return cls.name + "-" + spacy_model_name

    @staticmethod
    def _check_model_fallback(
        spacy_model_name: Union[str, None], language_name: str, warn: bool = False
    ):
        """This method checks if the `spacy_model_name` is missing.

        If it is missing, we will attempt a fallback. This feature is a measure
        to support spaCy 3.0 without breaking on users. In the future
        spaCy will no longer support `spacy link`.
        """
        if not spacy_model_name:
            fallback_mapping = {
                "zh": "zh_core_web_md",
                "da": "da_core_news_md",
                "nl": "nl_core_news_md",
                "en": "en_core_web_md",
                "fr": "fr_core_news_md",
                "de": "de_core_news_sm",
                "el": "el_core_news_md",
                "it": "it_core_news_md",
                "ja": "ja_core_news_md",
                "lt": "lt_core_news_md",
                "mk": "mk_core_news_md",
                "nb": "nb_core_news_md",
                "pl": "pl_core_news_md",
                "pt": "pt_core_news_md",
                "ro": "ro_core_news_md",
                "ru": "ru_core_news_md",
                "es": "es_core_news_md",
            }
            if language_name not in fallback_mapping.keys():
                raise InvalidModelError(
                    f"There is no fallback model for language '{language_name}'. "
                    f"Please add a `model` property to `SpacyNLP` manually to prevent this. "
                    f"More informaton can be found on {DOCS_URL_COMPONENTS}#spacynlp"
                )

            spacy_model_name = fallback_mapping[language_name]
            if warn:
                message = (
                    f"SpaCy model is not properly configured! Please add a `model` property to `SpacyNLP`. "
                    f"Will use '{spacy_model_name}' as a fallback spaCy model. "
                    f"This fallback will be deprecated in Rasa 3.0"
                )
                rasa.shared.utils.io.raise_deprecation_warning(
                    message=message, docs=f"{DOCS_URL_COMPONENTS}#spacynlp"
                )
        return spacy_model_name

    def provide_context(self) -> Dict[Text, Any]:
        """Creates a context dictionary from spaCy nlp object."""
        return {"spacy_nlp": self.nlp}

    def doc_for_text(self, text: Text) -> "Doc":
        """Makes a spaCy doc object from a string of text."""
        return self.nlp(self.preprocess_text(text))

    def preprocess_text(self, text: Optional[Text]) -> Text:
        """Processes the text before it is handled by spaCy."""
        if text is None:
            # converted to empty string so that it can still be passed to spacy.
            # Another option could be to neglect tokenization of the attribute of
            # this example, but since we are processing in batch mode, it would
            # get complex to collect all processed and neglected examples.
            text = ""
        if self.component_config.get("case_sensitive"):
            return text
        else:
            return text.lower()

    def get_text(self, example: Dict[Text, Any], attribute: Text) -> Text:

        return self.preprocess_text(example.get(attribute))

    @staticmethod
    def merge_content_lists(
        indexed_training_samples: List[Tuple[int, Text]],
        doc_lists: List[Tuple[int, "Doc"]],
    ) -> List[Tuple[int, "Doc"]]:
        """Merge lists with processed Docs back into their original order."""

        dct = dict(indexed_training_samples)
        dct.update(dict(doc_lists))
        return sorted(dct.items())

    @staticmethod
    def filter_training_samples_by_content(
        indexed_training_samples: List[Tuple[int, Text]]
    ) -> Tuple[List[Tuple[int, Text]], List[Tuple[int, Text]]]:
        """Separates empty training samples from content bearing ones."""

        docs_to_pipe = list(
            filter(
                lambda training_sample: training_sample[1] != "",
                indexed_training_samples,
            )
        )
        empty_docs = list(
            filter(
                lambda training_sample: training_sample[1] == "",
                indexed_training_samples,
            )
        )
        return docs_to_pipe, empty_docs

    def process_content_bearing_samples(
        self, samples_to_pipe: List[Tuple[int, Text]]
    ) -> List[Tuple[int, "Doc"]]:
        """Sends content bearing training samples to spaCy's pipe."""

        docs = [
            (to_pipe_sample[0], doc)
            for to_pipe_sample, doc in zip(
                samples_to_pipe,
                [
                    doc
                    for doc in self.nlp.pipe(
                        [txt for _, txt in samples_to_pipe], batch_size=50
                    )
                ],
            )
        ]
        return docs

    def process_non_content_bearing_samples(
        self, empty_samples: List[Tuple[int, Text]]
    ) -> List[Tuple[int, "Doc"]]:
        """Creates empty Doc-objects from zero-lengthed training samples strings."""

        from spacy.tokens import Doc

        n_docs = [
            (empty_sample[0], doc)
            for empty_sample, doc in zip(
                empty_samples, [Doc(self.nlp.vocab) for doc in empty_samples]
            )
        ]
        return n_docs

    def docs_for_training_data(
        self, training_data: TrainingData
    ) -> Dict[Text, List[Any]]:
        attribute_docs = {}
        for attribute in DENSE_FEATURIZABLE_ATTRIBUTES:

            texts = [
                self.get_text(e, attribute) for e in training_data.training_examples
            ]
            # Index and freeze indices of the training samples for preserving the order
            # after processing the data.
            indexed_training_samples = [(idx, text) for idx, text in enumerate(texts)]

            samples_to_pipe, empty_samples = self.filter_training_samples_by_content(
                indexed_training_samples
            )

            content_bearing_docs = self.process_content_bearing_samples(samples_to_pipe)

            non_content_bearing_docs = self.process_non_content_bearing_samples(
                empty_samples
            )

            attribute_document_list = self.merge_content_lists(
                indexed_training_samples,
                content_bearing_docs + non_content_bearing_docs,
            )

            # Since we only need the training samples strings,
            # we create a list to get them out of the tuple.
            attribute_docs[attribute] = [doc for _, doc in attribute_document_list]
        return attribute_docs

    def train(
        self,
        training_data: TrainingData,
        config: Optional[RasaNLUModelConfig] = None,
        **kwargs: Any,
    ) -> None:

        attribute_docs = self.docs_for_training_data(training_data)

        for attribute in DENSE_FEATURIZABLE_ATTRIBUTES:

            for idx, example in enumerate(training_data.training_examples):
                example_attribute_doc = attribute_docs[attribute][idx]
                if len(example_attribute_doc):
                    # If length is 0, that means the initial text feature
                    # was None and was replaced by ''
                    # in preprocess method
                    example.set(SPACY_DOCS[attribute], example_attribute_doc)

    def process(self, message: Message, **kwargs: Any) -> None:
        for attribute in DENSE_FEATURIZABLE_ATTRIBUTES:
            if message.get(attribute):
                message.set(
                    SPACY_DOCS[attribute], self.doc_for_text(message.get(attribute))
                )

    @classmethod
    def load(
        cls,
        meta: Dict[Text, Any],
        model_dir: Text = None,
        model_metadata: "Metadata" = None,
        cached_component: Optional["SpacyNLP"] = None,
        **kwargs: Any,
    ) -> "SpacyNLP":

        if cached_component:
            return cached_component

        model_name = cls._check_model_fallback(
            meta.get("model"), model_metadata.language, warn=True
        )

        nlp = cls.load_model(model_name)
        cls.ensure_proper_language_model(nlp)
        return cls(meta, nlp)

    @staticmethod
    def ensure_proper_language_model(nlp: Optional["Language"]) -> None:
        """Checks if the spacy language model is properly loaded.

        Raises an exception if the model is invalid."""

        if nlp is None:
            raise Exception(
                "Failed to load spacy language model. "
                "Loading the model returned 'None'."
            )
        if nlp.path is None:
            # Spacy sets the path to `None` if
            # it did not load the model from disk.
            # In this case `nlp` is an unusable stub.
            raise Exception(
                f"Failed to load spacy language model for "
                f"lang '{nlp.lang}'. Make sure you have downloaded the "
                f"correct model (https://spacy.io/docs/usage/)."
                ""
            )
