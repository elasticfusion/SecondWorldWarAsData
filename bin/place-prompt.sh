#!/usr/bin/env bash

BOOK="BreakoutAndPursuit"
BOOKNAME="Breakout and Pursuit"
CHAPTER=1
SECTION=a
TYPE="place"
CHAPTERFOLDER="../$BOOK/data/prompts/chapter$CHAPTER"
REVIEWFOLDER="chapter$CHAPTER$SECTION-review"
TARGETFILE="00-chapter$CHAPTER$SECTION-$TYPE-review.yaml"
SOURCELINK="https://www.ibiblio.org/hyperwar/USA/USA-E-Breakout/USA-E-Breakout-$CHAPTER.html"
FOOTNOTELINK="https://www.ibiblio.org/hyperwar/USA/USA-E-Breakout/fn$CHAPTER.html"

mkdir -p $CHAPTERFOLDER/$REVIEWFOLDER
cp ../$BOOK/data/prompts/chapter$CHAPTER/chapter$CHAPTER$SECTION-content.md  $CHAPTERFOLDER/$REVIEWFOLDER
touch $CHAPTERFOLDER/$REVIEWFOLDER/chapter$CHAPTER$SECTION-$TYPE.json
cp ../$BOOK/data/prompts/$TYPE_description_of_data.yaml $CHAPTERFOLDER/$REVIEWFOLDER
cat  "../$BOOK/data/prompts/review.yaml" "$CHAPTERFOLDER/$REVIEWFOLDER/$TARGETFILE" "$CHAPTERFOLDER/$REVIEWFOLDER/event_description_of_data.yaml" "../$BOOK/data/prompts/json-structure-event.yaml" | tee "$CHAPTERFOLDER/$REVIEWFOLDER/$TARGETFILE"
sed -i '' "s/#bookname#/$BOOKNAME/" "$CHAPTERFOLDER/$REVIEWFOLDER/$TARGETFILE"
sed -i '' "s/#chapter#/$CHAPTER/g" "$CHAPTERFOLDER/$REVIEWFOLDER/$TARGETFILE"
sed -i '' "s/#localsource#/chapter$CHAPTER$SECTION-content.md/g" "$CHAPTERFOLDER/$REVIEWFOLDER/$TARGETFILE"
rm -f "$CHAPTERFOLDER/$REVIEWFOLDER/$TYPE_description_of_data.yaml"
exit

