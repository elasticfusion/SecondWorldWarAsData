#!/usr/bin/env bash

CHAPTER=1
SECTION=a
TIMEREVIEWFOLDER="../data/prompts/chapter$CHAPTER/chapter$CHAPTER-$SECTION-timereview"

mkdir -p $TIMEREVIEWFOLDER
cp ../data/chapter$CHAPTER$SECTION.json $TIMEREVIEWFOLDER
cp ../data/prompts/json-structure.yaml $TIMEREVIEWFOLDER
cp ../data/prompts/timedate.yaml $TIMEREVIEWFOLDER
cat ../data/prompts/chapter$CHAPTER/chapter$CHAPTER-$SECTION-review.yaml ../data/prompts/timedatereview.yaml | tee $TIMEREVIEWFOLDER/chapter$CHAPTER-$SECTION-timereview.yaml

