#!/usr/bin/env bash

BOOK="BreakoutAndPursuit"
BOOKNAME="Breakout and Pursuit"
CHAPTER=1
SECTION=a
CHAPTERFOLDER="../$BOOK/data/prompts/chapter$CHAPTER"
REVIEWFOLDER="chapter$CHAPTER$SECTION-review"
TARGETFILE="00-chapter$CHAPTER$SECTION-review.yaml"
SOURCELINK="https://www.ibiblio.org/hyperwar/USA/USA-E-Breakout/USA-E-Breakout-$CHAPTER.html"
FOOTNOTELINK="https://www.ibiblio.org/hyperwar/USA/USA-E-Breakout/fn$CHAPTER.html"

mkdir -p $CHAPTERFOLDER/$REVIEWFOLDER
cp ../$BOOK/data/prompts/chapter$CHAPTER/chapter$CHAPTER$SECTION-content.md  $CHAPTERFOLDER/$REVIEWFOLDER
cp ../$BOOK/data/chapter$CHAPTER$SECTION.json $CHAPTERFOLDER/$REVIEWFOLDER
cp ../$BOOK/data/prompts/json-structure.yaml $CHAPTERFOLDER/$REVIEWFOLDER
#cp ../$BOOK/data/prompts/timedate.yaml $CHAPTERFOLDER/$REVIEWFOLDER/
cp ../$BOOK/data/prompts/description_of_data.yaml $CHAPTERFOLDER/$REVIEWFOLDER/
cp ../$BOOK/data/prompts/review.yaml $CHAPTERFOLDER/$REVIEWFOLDER/$TARGETFILE
sed -i '' "s/#bookname#/$BOOKNAME/" "$CHAPTERFOLDER/$REVIEWFOLDER/$TARGETFILE"
sed -i '' "s/#chapter#/$CHAPTER/g" "$CHAPTERFOLDER/$REVIEWFOLDER/$TARGETFILE"

exit


