#!/bin/bash

time ../bin/nlp4py-tagger --train tiger.model <(cut -f1,3 /data/Thomas/tiger_release_aug07.txt)
time ../bin/nlp4py-tagger --train tiger+empirist.model --prior tiger.model ~/Documents/Arbeit/nlp4py/data/empirist_gold_standard/train_all_sent_emo.txt
../bin/nlp4py-tagger --evaluate tiger+empirist.model ~/Documents/Arbeit/nlp4py/data/empirist_gold_standard/test_cmc_sent_emo.txt
../bin/nlp4py-tagger --evaluate tiger+empirist.model ~/Documents/Arbeit/nlp4py/data/empirist_gold_standard/test_web_sent_emo.txt
