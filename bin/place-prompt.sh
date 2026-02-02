#!/usr/bin/env bash

BOOK="BreakoutAndPursuit"
BOOKNAME="Breakout and Pursuit"
CHAPTER=4
SECTION=a
TYPE="place"
LOCALSOURCE="chapter$CHAPTER$SECTION-event.json"
CHAPTERFOLDER="../$BOOK/data/prompts/chapter$CHAPTER"
REVIEWFOLDER="chapter$CHAPTER$SECTION-review"
TARGETFILE="00-chapter$CHAPTER$SECTION-$TYPE-review.yaml"
SOURCELINK="https://www.ibiblio.org/hyperwar/USA/USA-E-Breakout/USA-E-Breakout-$CHAPTER.html"
FOOTNOTELINK="https://www.ibiblio.org/hyperwar/USA/USA-E-Breakout/fn$CHAPTER.html"


mkdir -p $CHAPTERFOLDER/$REVIEWFOLDER
cp ../$BOOK/data/prompts/chapter$CHAPTER/chapter$CHAPTER$SECTION-content.md  $CHAPTERFOLDER/$REVIEWFOLDER
touch $CHAPTERFOLDER/$REVIEWFOLDER/chapter$CHAPTER$SECTION-$TYPE.json
#cp ../$BOOK/data/prompts/$TYPE_description_of_data.yaml $CHAPTERFOLDER/$REVIEWFOLDER
cat  "../$BOOK/data/prompts/review.yaml" "../$BOOK/data/prompts/${TYPE}_description_of_data.yaml" "../$BOOK/data/prompts/json-structure-$TYPE.yaml" | tee "$CHAPTERFOLDER/$REVIEWFOLDER/$TARGETFILE"
sed -i '' "s/#bookname#/$BOOKNAME/" "$CHAPTERFOLDER/$REVIEWFOLDER/$TARGETFILE"
sed -i '' "s/#chapter#/$CHAPTER/g" "$CHAPTERFOLDER/$REVIEWFOLDER/$TARGETFILE"
sed -i '' "s/#localsource#/$LOCALSOURCE/g" "$CHAPTERFOLDER/$REVIEWFOLDER/$TARGETFILE"
rm -f "$CHAPTERFOLDER/$REVIEWFOLDER/$TYPE_description_of_data.yaml"
exit

