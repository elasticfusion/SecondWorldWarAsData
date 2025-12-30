#!/usr/bin/env bash

BOOK="BreakoutAndPursuit"
BOOKNAME="Breakout and Pursuit"
CHAPTER=3
SECTION=c
CHAPTERFOLDER="../$BOOK/data/prompts/chapter$CHAPTER"
TIMEREVIEWFOLDER="chapter$CHAPTER$SECTION-timereview"
TARGETFILE="00-chapter$CHAPTER$SECTION-timedatereview.yaml"
SOURCELINK="https://www.ibiblio.org/hyperwar/USA/USA-E-Breakout/USA-E-Breakout-$CHAPTER.html"
FOOTNOTELINK="https://www.ibiblio.org/hyperwar/USA/USA-E-Breakout/fn$CHAPTER.html"

mkdir -p $CHAPTERFOLDER/$TIMEREVIEWFOLDER
cp ../$BOOK/data/prompts/chapter$CHAPTER/chapter$CHAPTER$SECTION-content.md  $CHAPTERFOLDER/$TIMEREVIEWFOLDER
cp ../$BOOK/data/chapter$CHAPTER$SECTION.json $CHAPTERFOLDER/$TIMEREVIEWFOLDER
cp ../$BOOK/data/prompts/json-structure.yaml $CHAPTERFOLDER/$TIMEREVIEWFOLDER
cp ../$BOOK/data/prompts/timedate.yaml $CHAPTERFOLDER/$TIMEREVIEWFOLDER/
cp ../$BOOK/data/prompts/timedatereview.yaml $CHAPTERFOLDER/$TIMEREVIEWFOLDER/$TARGETFILE
sed -i '' "s/#bookname#/$BOOKNAME/" "$CHAPTERFOLDER/$TIMEREVIEWFOLDER/$TARGETFILE"
sed -i '' "s/#chapter#/$CHAPTER/g" "$CHAPTERFOLDER/$TIMEREVIEWFOLDER/$TARGETFILE"

exit
