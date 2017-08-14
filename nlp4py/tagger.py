#!/usr/bin/env python3

import base64
import functools
import gzip
import itertools
import json
import logging
import math
import multiprocessing
import re
import statistics

import numpy as np

from nlp4py.averaged_structured_perceptron import AveragedStructuredPerceptron


class ASPTagger(AveragedStructuredPerceptron):
    """A part-of-speech tagger based on the averaged structured
    perceptron.

    """
    def __init__(self, beam_size, iterations, lexicon=None, mapping=None, brown_clusters=None, word_to_vec=None, prior_weights=None, prior_vocabulary=None):
        super().__init__(beam_size=beam_size, iterations=iterations, latent_features=None, prior_weights=prior_weights)
        if prior_vocabulary is None:
            self.vocabulary = set()
        elif isinstance(prior_vocabulary, set):
            self.vocabulary = prior_vocabulary
        else:
            self.vocabulary = set(prior_vocabulary)
        self.lexicon = lexicon
        self.mapping = mapping
        self.brown_clusters = brown_clusters
        self.word_to_vec = word_to_vec
        if self.mapping is not None:
            self.mapping["<START-2>"] = "<START>"
            self.mapping["<START-1>"] = "<START>"
        self.email = re.compile(r"^[[:alnum:].%+-]+(?:@| \[?at\]? )[[:alnum:].-]+(?:\.| \[?dot\]? )[[:alpha:]]{2,}$", re.IGNORECASE)
        self.xmltag = re.compile(r"^</?[^>]+>$")
        self.url = re.compile(r"^(?:(?:(?:https?|ftp|svn)://|(?:https?://)?www\.).+)|(?:[\w./-]+\.(?:de|com|org|net|edu|info|jpg|png|gif|log|txt)(?:-\w+)?)$", re.IGNORECASE)
        self.mention = re.compile(r"^@\w+$")
        self.hashtag = re.compile(r"^#\w+$")
        self.action_word = re.compile(r"^[*+][^*]+[*]$")
        self.punctuation = re.compile(r'^[](){}.!?…<>%‰€$£₤¥°@~*„“”‚‘"\'`´»«›‹,;:/*+=&%§~#^−–-]+$')
        self.ordinal = re.compile(r"^(?:\d+\.)+$")
        self.number = re.compile(r"""(?<!\w)
                                (?:[−+-]?              # optional sign
                                  \d*                  # optional digits before decimal point
                                  [.,]?                # optional decimal point
                                  \d+                  # digits
                                  (?:[eE][−+-]?\d+)?   # optional exponent
                                  |
                                  \d+[\d.,]*\d+)
                                (?![.,]?\d)""", re.VERBOSE)
        emoticon_set = set(["(-.-)", "(T_T)", "(♥_♥)", ")':", ")-:",
                            "(-:", ")=", ")o:", ")x", ":'C", ":/", ":<",
                            ":C", ":[", "=(", "=)", "=D", "=P", ">:",
                            "D':", "D:", "\:", "]:", "x(", "^^", "o.O",
                            "oO", "\O/", "\m/", ":;))", "_))", "*_*",
                            "._.", ":wink:", ">_<", "*<:-)", ":!:",
                            ":;-))"])
        emoticon_list = sorted(emoticon_set, key=len, reverse=True)
        self.emoticon = re.compile(r"""^(?:(?:[:;]|(?<!\d)8)           # a variety of eyes, alt.: [:;8]
                                    [-'oO]?                       # optional nose or tear
                                    (?: \)+ | \(+ | [*] | ([DPp])\1*(?!\w)))   # a variety of mouths
                                    """ +
                                   r"|" +
                                   r"(?:xD+|XD+)" +
                                   r"|" +
                                   r"([:;])[ ]+([()])" +
                                   r"|" +
                                   r"\^3"
                                   r"|" +
                                   r"|".join([re.escape(_) for _ in emoticon_list]) +
                                   r"$", re.VERBOSE)
        # Unicode emoticons and other symbols
        self.emoji = re.compile(r"^[\u2600-\u27BF\U0001F300-\U0001f64f\U0001F680-\U0001F6FF\U0001F900-\U0001F9FF]$")

    def train(self, words, tags, lengths):
        """"""
        self.vocabulary.update(set(words))
        self.latent_features = functools.partial(self._get_latent_features, [w.lower() for w in words])
        X = self._get_static_features(words, lengths)
        self.fit(X, tags, lengths)

    def tag(self, words, lengths):
        """"""
        self.latent_features = functools.partial(self._get_latent_features, [w.lower() for w in words])
        X = self._get_static_features(words, lengths)
        tags = self.predict(X, lengths)
        start = 0
        for length, local_tags in zip(lengths, tags):
            local_words = words[start:start + length]
            start += length
            yield zip(local_words, local_tags)

    def evaluate(self, words, tags, lengths):
        """"""
        self.latent_features = functools.partial(self._get_latent_features, [w.lower() for w in words])
        X = self._get_static_features(words, lengths)
        # accuracy = self.score(X, tags, lengths)
        # return accuracy
        predicted = self.predict(X, lengths)
        correct, correct_iv, correct_oov = 0, 0, 0
        total, total_iv, total_oov = 0, 0, 0
        start = 0
        for length, local_pred in zip(lengths, predicted):
            local_words = words[start:start + length]
            local_gold = tags[start:start + length]
            start += length
            for w, g, p in zip(local_words, local_gold, local_pred):
                total += 1
                if w in self.vocabulary:
                    total_iv += 1
                    if g == p:
                        correct += 1
                        correct_iv += 1
                else:
                    total_oov += 1
                    if g == p:
                        correct += 1
                        correct_oov += 1
        accuracy = correct / total
        try:
            accuracy_iv = correct_iv / total_iv
        except ZeroDivisionError:
            accuracy_iv = 0
        try:
            accuracy_oov = correct_oov / total_oov
        except ZeroDivisionError:
            accuracy_oov = 0
        return accuracy, accuracy_iv, accuracy_oov

    def crossvalidate(self, words, tags, lengths):
        """"""
        sentence_ranges = list(zip((a - b for a, b in zip(itertools.accumulate(lengths), lengths)), lengths))
        X = self._get_static_features(words, lengths)
        div, mod = divmod(len(sentence_ranges), 10)
        cvi = functools.partial(self._cross_val_iteration,
                                words=words, X=X, y=tags,
                                lengths=lengths,
                                sentence_ranges=sentence_ranges,
                                div=div, mod=mod)
        with multiprocessing.Pool() as pool:
            accuracies = pool.map(cvi, range(10))
        # accuracies = list(map(cvi, range(10)))
        return statistics.mean(accuracies), 2 * statistics.stdev(accuracies)

    def save(self, filename):
        """"""
        with gzip.open(filename, 'wb') as f:
            features = sorted(self.weights.keys())
            f.write("[\n".encode())
            f.write(json.dumps(list(self.vocabulary), ensure_ascii=False, indent=4).encode())
            f.write(",\n".encode())
            f.write(json.dumps(self.lexicon, ensure_ascii=False, indent=4).encode())
            f.write(",\n".encode())
            f.write(json.dumps(self.mapping, ensure_ascii=False, indent=4).encode())
            f.write(",\n".encode())
            f.write(json.dumps(self.brown_clusters, ensure_ascii=False, indent=4).encode())
            f.write(",\n".encode())
            f.write(json.dumps(self.word_to_vec, ensure_ascii=False, indent=4).encode())
            f.write(",\n".encode())
            f.write(json.dumps(self.target_mapping, ensure_ascii=False, indent=4).encode())
            f.write(",\n".encode())
            f.write(str(self.target_size).encode())
            f.write(",\n".encode())
            f.write(json.dumps(features, ensure_ascii=False, indent=4).encode())
            f.write(",\n".encode())
            f.write("[\n".encode())
            for feat in features[:-1]:
                f.write('"'.encode())
                f.write(base64.b85encode(self.weights[feat].tostring()))
                f.write('",\n'.encode())
            f.write('"'.encode())
            f.write(base64.b85encode(self.weights[features[-1]].tostring()))
            f.write('"\n]\n'.encode())
            f.write("]\n".encode())

    def load(self, filename):
        """"""
        with gzip.open(filename, 'rb') as f:
            model = json.loads(f.read().decode())
            vocabulary, self.lexicon, self.mapping, self.brown_clusters, self.word_to_vec, self.target_mapping, self.target_size, features, weights = model
            self.vocabulary = set(vocabulary)
            self.weights = {f: np.fromstring(base64.b85decode(w), np.float64) for f, w in zip(features, weights)}

    def _cross_val_iteration(self, i, words, X, y, lengths, sentence_ranges, div, mod):
        """"""
        test_ranges = sentence_ranges[i * div + min(i, mod):(i + 1) * div + min(i + 1, mod)]
        test_start = test_ranges[0][0]
        test_end = test_ranges[-1][0] + test_ranges[-1][1]
        test_lengths = lengths[i * div + min(i, mod):(i + 1) * div + min(i + 1, mod)]
        train_lengths = lengths[:i * div + min(i, mod)] + lengths[(i + 1) * div + min(i + 1, mod):]
        test_words = words[test_start:test_end]
        test_X = X[test_start:test_end]
        test_y = y[test_start:test_end]
        train_words = words[:test_start] + words[test_end:]
        train_X = X[:test_start] + X[test_end:]
        train_y = y[:test_start] + y[test_end:]
        # train
        self.latent_features = functools.partial(self._get_latent_features, [w.lower() for w in train_words])
        self.fit(train_X, train_y, train_lengths)
        # evaluate
        self.latent_features = functools.partial(self._get_latent_features, [w.lower() for w in test_words])
        accuracy = self.score(test_X, test_y, test_lengths)
        logging.info("Accuracy: %.2f%%" % (accuracy * 100,))
        return accuracy

    def _get_static_features(self, words, lengths):
        """"""
        lexicon = self.lexicon
        brown_clusters = self.brown_clusters
        word_to_vec = self.word_to_vec
        features = []
        start = 0
        for length in lengths:
            sentence = words[start:start + length]
            start += length
            tokens = ["<START-2>", "<START-1>"] + [w.lower() for w in sentence] + ["<END+1>", "<END+2>"]
            for i, word in enumerate(sentence):
                j = i + 2
                local_features = []
                w = tokens[j]
                p1 = tokens[j - 1]
                p2 = tokens[j - 2]
                n1 = tokens[j + 1]
                n2 = tokens[j + 2]
                # constant bias feature acts like a prior
                local_features.append("bias")
                # rounded logarithm of word length
                local_features.append("W_loglength: %d" % round(math.log(len(word))))
                # current word
                local_features.append("W_word: %s" % w)
                # next words
                local_features.append("N1_word: %s" % n1)
                local_features.append("N2_word: %s" % n2)
                # affixes
                local_features.append("W_prefix: %s" % w[:3])
                local_features.append("W_suffix: %s" % w[-3:])
                if i >= 1:
                    local_features.append("P1_suffix: %s" % p1[-3:])
                if length - i > 1:
                    local_features.append("N1_suffix: %s" % n1[-3:])
                # word shape
                local_features.append("W_shape: %s" % self._word_shape(word))
                # Flags
                if i >= 2:
                    local_features.extend(self._word_flags(p2, "P2"))
                if i >= 1:
                    local_features.extend(self._word_flags(p1, "P1"))
                local_features.extend(self._word_flags(w, "W"))
                if length - i > 1:
                    local_features.extend(self._word_flags(n1, "N1"))
                if length - i > 2:
                    local_features.extend(self._word_flags(n2, "N2"))
                # Brown clusters
                if brown_clusters is not None:
                    # P2, P1, W, N1, N2
                    if i >= 2:
                        bc, freq = brown_clusters.get(p2, ("N/A", 0))
                        local_features.append("P2_brown: %s" % bc)
                    if i >= 1:
                        bc, freq = brown_clusters.get(p1, ("N/A", 0))
                        local_features.append("P1_brown: %s" % bc)
                    bc, freq = brown_clusters.get(w, ("N/A", 0))
                    local_features.append("W_brown: %s" % bc)
                    local_features.append("W_logfreq: %d" % freq)
                    if length - i > 1:
                        bc, freq = brown_clusters.get(n1, ("N/A", 0))
                        local_features.append("N1_brown: %s" % bc)
                    if length - i > 2:
                        bc, freq = brown_clusters.get(n2, ("N/A", 0))
                        local_features.append("N2_brown: %s" % bc)
                if word_to_vec is not None:
                    # if w in word_to_vec:
                    #     for i, d in enumerate(word_to_vec[w]):
                    #         local_features.append("W_w2v_%d: %d" % (i, round(float(d))))
                    if w in word_to_vec:
                        local_features.append("W_w2v: %s" % word_to_vec[w])
                if lexicon is not None:
                    if w in lexicon:
                        for feat in lexicon[w]:
                            local_features.append("W_lex: %s" % feat)
                    else:
                        local_features.append("W_lex: N/A")
                features.append(local_features)
        return features

    def _get_latent_features(self, words, start, beam, i):
        """"""
        features = []
        global_i = start + i
        tags = ["<START-2>", "<START-1>"] + beam
        mapping = self.mapping
        j = i + 2
        if i >= 1:
            features.append("P1_word, P1_pos: %s, %s" % (words[global_i - 1], tags[j - 1]))
            if mapping is not None:
                features.append("P1_word, P1_wc: %s, %s" % (words[global_i - 1], mapping[tags[j - 1]]))
        if i >= 2:
            features.append("P2_word, P2_pos: %s, %s" % (words[global_i - 2], tags[j - 2]))
            if mapping is not None:
                features.append("P2_word, P2_wc: %s, %s" % (words[global_i - 2], mapping[tags[j - 2]]))
        features.append("P1_pos: %s" % tags[j - 1])
        features.append("P2_pos: %s" % tags[j - 2])
        features.append("P2_pos, P1_pos: %s, %s" % (tags[j - 2], tags[j - 1]))
        features.append("P1_pos, W_word: %s, %s" % (tags[j - 1], words[global_i]))
        if mapping is not None:
            features.append("P1_wc: %s" % mapping[tags[j - 1]])
            features.append("P2_wc: %s" % mapping[tags[j - 2]])
            features.append("P2_wc, P1_wc: %s, %s" % (mapping[tags[j - 2]], mapping[tags[j - 1]]))
            features.append("P1_wc, W_word: %s, %s" % (mapping[tags[j - 1]], words[global_i]))
        return features

    @staticmethod
    @functools.lru_cache(maxsize=10240)
    def _word_shape(word):
        if len(word) >= 100:
            return "LONG"
        shape = []
        last = ""
        shape_char = ""
        seq = 0
        for c in word:
            if c.isalpha():
                if c.isupper():
                    shape_char = "X"
                else:
                    shape_char = "x"
            elif c.isdigit():
                shape_char = "d"
            else:
                shape_char = c
            if shape_char == last:
                seq += 1
            else:
                seq = 0
                last = shape_char
            if seq < 4:
                shape.append(shape_char)
        return "".join(shape)

    @functools.lru_cache(maxsize=10240)
    def _word_flags(self, word, prefix):
        """"""
        flags = []
        if word.isalpha():
            flags.append("%s_isalpha" % prefix)
        if word.isnumeric():
            flags.append("%s_isnumeric" % prefix)
        if word.islower():
            flags.append("%s_islower" % prefix)
        if word.isupper():
            flags.append("%s_isupper" % prefix)
        if word.istitle():
            flags.append("%s_istitle" % prefix)
        if self.email.search(word):
            flags.append("%s_isemail" % prefix)
        if self.xmltag.search(word):
            flags.append("%s_istag" % prefix)
        if self.url.search(word):
            flags.append("%s_isurl" % prefix)
        if self.mention.search(word):
            flags.append("%s_ismention" % prefix)
        if self.hashtag.search(word):
            flags.append("%s_ishashtag" % prefix)
        if self.action_word.search(word):
            flags.append("%s_isactword" % prefix)
        if self.emoticon.search(word):
            flags.append("%s_isemoticon" % prefix)
        if self.emoji.search(word):
            flags.append("%s_isemoji" % prefix)
        if self.punctuation.search(word):
            flags.append("%s_ispunct" % prefix)
        if self.ordinal.search(word):
            flags.append("%s_isordinal" % prefix)
        if self.number.search(word):
            flags.append("%s_isnumber" % prefix)
        return flags
