#!/usr/bin/env python3

## Model embedding + LSTM + Structured Attention
# Copyright (C) <2018-2022>  <Agence Data Services, DSI Pôle Emploi>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
#
# Classes :
# - ModelEmbeddingLstmStructuredAttention -> Model for predictions via embedding + LSTM + structured attention -> useful to get predictions explenation. Based on Gaëlle JOUIS Thesis. Work in progress.


# ** EXPERIMENTAL **
# ** EXPERIMENTAL **
# ** EXPERIMENTAL **


import os
import json
import pickle
import logging
import shutil
import numpy as np
import pandas as pd
import seaborn as sns
from typing import Union, Any, List, Callable

import tensorflow as tf
from tensorflow.keras.models import Model
from tensorflow.keras.optimizers import Adam
from tensorflow.keras.preprocessing.text import Tokenizer
from tensorflow.keras.models import load_model as load_model_keras
from tensorflow.keras.layers import Lambda, Dense, Input, Embedding, LSTM, Bidirectional

from {{package_name}} import utils
from {{package_name}}.models_training import utils_deep_keras
from {{package_name}}.models_training.model_keras import ModelKeras
from {{package_name}}.models_training.utils_deep_keras import AttentionAverage

sns.set(style="darkgrid")


class ModelEmbeddingLstmStructuredAttention(ModelKeras):
    '''Model for predictions via embedding + LSTM + structured attention -> useful to get predictions explenation.
    Based on Gaëlle JOUIS Thesis.
    Work in progress.
    '''

    _default_name = 'model_embedding_lstm_structured_attention'

    def __init__(self, max_sequence_length: int = 200, max_words: int = 100000,
                 padding: str = 'pre', truncating: str = 'post', oov_token: str = "oovt",
                 tokenizer_filters: str = "’!#$%&()*+,-./:;<=>?@[\\]^_`{|}~\t\n\r\'\"", **kwargs) -> None:
        '''Initialization of the class (see ModelClass & ModelKeras for more arguments)

        Kwargs:
            max_sequence_length (int): Maximum number of words per sequence (ie. sentences)
            max_words (int): Maximum number of words for tokenization
            padding (str): Padding (add zeros) at the beginning ('pre') or at the end ('post') of the sequences
            truncating (str): Truncating the beginning ('pre') or the end ('post') of the sequences (if superior to max_sequence_length)
            oov_token (str): Out Of Vocabulary token (to be used with the Tokenizer)
            tokenizer_filters (str): Filter to use by the tokenizer
        Raises:
            ValueError: If the object padding is not a valid choice (['pre', 'post'])
            ValueError: If the object truncating is not a valid choice (['pre', 'post'])
        '''
        if padding not in ['pre', 'post']:
            raise ValueError(f"The object padding ({padding}) is not a valid choice (['pre', 'post'])")
        if truncating not in ['pre', 'post']:
            raise ValueError(f"The object truncating ({truncating}) is not a valid choice (['pre', 'post'])")
        # Init.
        super().__init__(**kwargs)

        # Get logger (must be done after super init)
        self.logger = logging.getLogger(__name__)

        self.max_sequence_length = max_sequence_length
        self.max_words = max_words
        self.padding = padding
        self.truncating = truncating
        self.oov_token = oov_token

        # Tokenizer set on fit
        self.tokenizer: Any = None
        self.tokenizer_filters = tokenizer_filters

    @utils.data_agnostic_str_to_list
    @utils.trained_needed
    def predict_proba(self, x_test, experimental_version: bool = False, **kwargs) -> np.ndarray:
        '''Predicts probabilities on the test dataset

        Warning, this provides probabilities for a single model. If we use nb_iter > 1, we must use predict(return_proba=True)

        Args:
            x_test (?): Array-like or sparse matrix, shape = [n_samples, n_features]
        Kwargs:
            experimental_version (bool): If an experimental (but faster) version must be used
        Returns:
            (np.ndarray): Array, shape = [n_samples, n_classes]
        '''
        # Prepare input
        x_test = self._prepare_x_test(x_test)
        # Process
        if experimental_version:
            return self.experimental_predict_proba(x_test)
        else:
            return self.model.predict(x_test, batch_size=128, verbose=1)  # type: ignore
       

    def _prepare_x_train(self, x_train) -> np.ndarray:
        '''Prepares the input data for the model. Called when fitting the model

        Args:
            x_train (?): Array-like, shape = [n_samples, n_features]
        Returns:
            (np.ndarray): Prepared data
        '''
        # Get tokenizer & fit on train
        self.tokenizer = Tokenizer(num_words=self.max_words, filters=self.tokenizer_filters, oov_token=self.oov_token)
        self.logger.info('Fitting the tokenizer')
        self.tokenizer.fit_on_texts(x_train)
        return self._get_sequence(x_train, self.tokenizer, self.max_sequence_length, padding=self.padding, truncating=self.truncating)

    def _prepare_x_test(self, x_test, max_sequence_length: int = 0) -> Any:
        '''Prepares the input data for the model. Called when fitting the model

        Args:
            x_test (?): Array-like, shape = [n_samples, n_features]
        Kwargs:
            max_sequence_length (int): Maximum number of words per sequence (ie. sentences)
                Default to self.max_sequence_length.
                Useful only with explanations.
                We don't use 'None' as it is a particular usage for _get_sequence.
                Hence we backup on default value if max_sequence_length is 0.
        Returns:
            (np.ndarray): Prepared data
        '''
        if max_sequence_length == 0:
            max_sequence_length = self.max_sequence_length
        # Get sequences on test (already fitted on train)
        return self._get_sequence(x_test, self.tokenizer, max_sequence_length, padding=self.padding, truncating=self.truncating)

    def _get_model(self, custom_tokenizer=None) -> Any:
        '''Gets a model structure

        Kwargs:
            custom_tokenizer (?): Tokenizer (if different from the one of the class). Permits to manage "new embeddings"
        Returns:
            (Model): a Keras model
        '''
        # Get parameters
        lstm_units = self.keras_params['lstm_units'] if 'lstm_units' in self.keras_params.keys() else 50  # u = 50 in the GIT implementation, 300 in the paper (YELP)
        dense_size = self.keras_params['dense_size'] if 'dense_size' in self.keras_params.keys() else 300  # d_a = 100 in the GIT implementation, 350 in the paper (YELP)
        attention_hops = self.keras_params['attention_hops'] if 'attention_hops' in self.keras_params.keys() else 1  # r = 10 in the GIT implementation, 30 in the paper (YELP)
        lr = self.keras_params['lr'] if 'lr' in self.keras_params.keys() else 0.01

        # Start by getting embedding matrix
        if custom_tokenizer is not None:
            embedding_matrix, embedding_size = self._get_embedding_matrix(custom_tokenizer)
        else:
            embedding_matrix, embedding_size = self._get_embedding_matrix(self.tokenizer)

        # Get input dim
        input_dim = embedding_matrix.shape[0]

        # Get model
        num_classes = len(self.list_classes)

        # Process
        words = Input(shape=(self.max_sequence_length,))
        x = Embedding(input_dim, embedding_size, weights=[embedding_matrix], trainable=False)(words)
        h = Bidirectional(LSTM(lstm_units, return_sequences=True))(x)
        x = Dense(dense_size, activation='tanh')(h)  # tanh(W_{S1}*H^T) , H^T = x (LSTM output), dim = d_a*2u
        a = Dense(attention_hops, activation=utils_deep_keras.softmax_axis)(x)  # softmax(W_{s2}*X) = A
        at = tf.transpose(a, perm=[0, 2, 1], name="attention_layer")  # At, used in Kaushalshetty project, output dim = (r,n)
        # Trick to name the attention layer (does not work with TensorFlow layers)
        # https://github.com/keras-team/keras/issues/6194#issuecomment-416365112
        at_identity = Lambda(lambda x: x, name="attention_layer")(at)
        m = at_identity @ h  # M = AH
        x = AttentionAverage(attention_hops)(m)

        # Last layer
        activation = 'sigmoid' if self.multi_label else 'softmax'
        out = Dense(num_classes, activation=activation, kernel_initializer='glorot_uniform')(x)

        # Compile model
        model = Model(inputs=words, outputs=[out])
        optimizer = Adam(lr=lr)
        # optimizer = SGD(lr=0.06, clipnorm=0.5)  # paper: 0.06 YELP
        loss = utils_deep_keras.f1_loss if self.multi_label else 'categorical_crossentropy'
        metrics: List[Union[str, Callable]] = ['accuracy'] if not self.multi_label else ['categorical_accuracy', 'categorical_crossentropy', utils_deep_keras.f1, utils_deep_keras.precision, utils_deep_keras.recall, utils_deep_keras.f1_loss]
        model.compile(optimizer=optimizer, loss=loss, metrics=metrics)
        if self.logger.getEffectiveLevel() < logging.ERROR:
            model.summary()

        # Try to save model as png if level_save > 'LOW'
        if self.level_save in ['MEDIUM', 'HIGH']:
            self._save_model_png(model)

        # Return
        return model

    @utils.data_agnostic_str_to_list
    @utils.trained_needed
    def explain(self, x_test, attention_threshold: float = 0.15, fix_index: bool = False) -> list:
        '''Predictions on test set, with all attentions weights
        -> explanations on preprocessed words

        This function returns a list of dictionnaries:
            {
                index: (word, value)
            }
        where:
            - index: word index in the sequence (after tokenization & padding)
            - word: the corresponding word after preprocessing
            - value: the attention value for this word

        Precision: if fix_index is set to True, the output indexes correspond to the word index in the preprocessed sentence (and not in the sequence)

        Args:
            x_test (?): Array-like or sparse matrix, shape = [n_samples]
                WARNING : sentences must be preprocessed here
        Kwargs:
            attention_threshold (float): Minimum attention threshold
            fix_index (bool): If we have to fix sequences index to get word indexes in the preprocessed sentence
        Returns:
            list: List of dictionnaries (one entry per sentence) with matched words (i.e. attention > threshold)
        '''
        # Cast en pd.Series
        x_test = pd.Series(x_test)

        # Prepare input
        x_test_prep = self._prepare_x_test(x_test)

        # Retrieve attention scores
        intermediate_layer_model = Model(inputs=self.model.input, outputs=self.model.get_layer('attention_layer').output)  # type: ignore
        intermediate_output = intermediate_layer_model(x_test_prep)

        # Retrieve (word, attention) tuples
        intermediate_output_reshaped = tf.squeeze(intermediate_output)
        # Manage cases where x_test has only one element
        if len(intermediate_output_reshaped.shape) == 1:
            intermediate_output_reshaped = tf.expand_dims(intermediate_output_reshaped, axis=0)
        seq_q_attention = tf.stack([x_test_prep, intermediate_output_reshaped], axis=1)
        seq_q_attention = tf.transpose(seq_q_attention, [0, 2, 1])
        seq_q_attention = seq_q_attention.numpy()
        text_w_attention = [[(self.tokenizer.sequences_to_texts([[y[0]]]), y[1])  # type: ignore
                             if y[0] != 0 else ("<PAD>", y[1]) for y in entry]
                            for entry in seq_q_attention]
        # Filter words with low attention score
        selected_words = [{index: (tup[0][0], tup[1]) for index, tup in enumerate(entry) if tup[0] != '<PAD>' and tup[1] >= attention_threshold} for entry in text_w_attention]

        # If wanted, fix indexes
        if fix_index:
            # Process sentence per sentence
            for i, entry in enumerate(selected_words):
                # Case 1 : padding
                # Check for padding (at least one 0)
                nb_padding = len(np.where(x_test_prep[i] == 0)[0])
                padded = True if nb_padding != 0 else False
                if self.padding == 'pre':
                    if nb_padding != 0:
                        # We shift the sequence (i.e. we remove the padding)
                        selected_words[i] = {index - nb_padding: val for index, val in entry.items()}
                else:
                    pass  # We do nothing (already in correct ordre if post padding)
                # Case 2 : truncating
                if not padded:
                    if self.truncating == 'pre':
                        # We must reapply get_sequences with max sequence length to retrieve removed words
                        x_test_full = self._prepare_x_test([x_test[i]], max_sequence_length=None)[0]  # type: ignore
                        nb_truncating = len(x_test_full) - len(x_test_prep[i])
                        if nb_truncating != 0:
                            # We shift the sequence to take truncation in account
                            selected_words[i] = {index + nb_truncating: val for index, val in entry.items()}
                    else:
                        pass  # We do nothing (already in correct ordre if post truncating)

        # Returns
        return selected_words

    def _pad_text(self, text: list, pad_token: str = '<PAD>') -> list:
        '''Apply padding on a tokenized text (list)

        Args:
            text (list): List of tokenized words
        Kwargs:
            pad_token (str): Default pad token
        Returns:
            list: List of tokenized words, with padding / truncating management
        '''
        # If there is too much words, we truncate the sequence
        if len(text) > self.max_sequence_length:
            if self.truncating == 'post':
                text = text[:self.max_sequence_length]
            else:  # pre
                text = text[-self.max_sequence_length:]
        # If there is not enough words, we pad the sequence
        elif len(text) < self.max_sequence_length:
            padding_list = [pad_token for i in range(self.max_sequence_length - len(text))]
            if self.padding == 'pre':
                text = padding_list + text
            else:
                text = text + padding_list
        # Return
        return text

    def save(self, json_data: Union[dict, None] = None) -> None:
        '''Saves the model

        Kwargs:
            json_data (dict): Additional configurations to be saved
        '''
        # Save configuration JSON
        if json_data is None:
            json_data = {}

        # Add specific data
        json_data['max_sequence_length'] = self.max_sequence_length
        json_data['max_words'] = self.max_words
        json_data['padding'] = self.padding
        json_data['truncating'] = self.truncating
        json_data['oov_token'] = self.oov_token
        json_data['tokenizer_filters'] = self.tokenizer_filters

        # Save tokenizer if not None & level_save > LOW
        if (self.tokenizer is not None) and (self.level_save in ['MEDIUM', 'HIGH']):
            # Manage paths
            tokenizer_path = os.path.join(self.model_dir, "embedding_tokenizer.pkl")
            # Save as pickle
            with open(tokenizer_path, 'wb') as f:
                pickle.dump(self.tokenizer, f)

        # Save
        super().save(json_data=json_data)

    def reload_from_standalone(self, **kwargs) -> None:
        '''Reloads a model from its configuration and "standalones" files
        - /!\\ Experimental /!\\ -

        Kwargs:
            configuration_path (str): Path to configuration file
            hdf5_path (str): Path to hdf5 file
            tokenizer_path (str): Path to tokenizer file
        Raises:
            ValueError: If configuration_path is None
            ValueError: If hdf5_path is None
            ValueError: If tokenizer_path is None
            FileNotFoundError: If the object configuration_path is not an existing file
            FileNotFoundError: If the object hdf5_path is not an existing file
            FileNotFoundError: If the object tokenizer_path is not an existing file
        '''
        # Retrieve args
        configuration_path = kwargs.get('configuration_path', None)
        hdf5_path = kwargs.get('hdf5_path', None)
        tokenizer_path = kwargs.get('tokenizer_path', None)

        # Checks
        if configuration_path is None:
            raise ValueError("The argument configuration_path can't be None")
        if hdf5_path is None:
            raise ValueError("The argument hdf5_path can't be None")
        if tokenizer_path is None:
            raise ValueError("The argument tokenizer_path can't be None")
        if not os.path.exists(configuration_path):
            raise FileNotFoundError(f"The file {configuration_path} does not exist")
        if not os.path.exists(hdf5_path):
            raise FileNotFoundError(f"The file {hdf5_path} does not exist")
        if not os.path.exists(tokenizer_path):
            raise FileNotFoundError(f"The file {tokenizer_path} does not exist")

        # Load confs
        with open(configuration_path, 'r', encoding='{{default_encoding}}') as f:
            configs = json.load(f)
        # Can't set int as keys in json, so need to cast it after reloading
        # dict_classes keys are always ints
        if 'dict_classes' in configs.keys():
            configs['dict_classes'] = {int(k): v for k, v in configs['dict_classes'].items()}
        elif 'list_classes' in configs.keys():
            configs['dict_classes'] = {i: col for i, col in enumerate(configs['list_classes'])}

        # Set class vars
        # self.model_name = # Keep the created name
        # self.model_dir = # Keep the created folder
        self.nb_fit = configs.get('nb_fit', 1)  # Consider one unique fit by default
        self.trained = configs.get('trained', True)  # Consider trained by default
        # Try to read the following attributes from configs and, if absent, keep the current one
        for attribute in ['x_col', 'y_col', 'list_classes', 'dict_classes', 'multi_label', 'level_save',
                          'batch_size', 'epochs', 'validation_split', 'patience',
                          'embedding_name', 'max_sequence_length', 'max_words', 'padding',
                          'truncating', 'oov_token', 'tokenizer_filters', 'nb_iter_keras', 'keras_params']:
            setattr(self, attribute, configs.get(attribute, getattr(self, attribute)))

        # Reload model
        self.model = load_model_keras(hdf5_path, custom_objects=self.custom_objects)

        # Save best hdf5 in new folder
        new_hdf5_path = os.path.join(self.model_dir, 'best.hdf5')
        shutil.copyfile(hdf5_path, new_hdf5_path)

        # Reload tokenizer
        with open(tokenizer_path, 'rb') as f:
            self.tokenizer = pickle.load(f)


if __name__ == '__main__':
    logger = logging.getLogger(__name__)
    logger.error("This script is not stand alone but belongs to a package that has to be imported.")
