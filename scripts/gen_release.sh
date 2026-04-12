#!/bin/bash

pushd .. > /dev/null

# Create Autona dir and its stage_strike_app folders
mkdir -p Autona/stage_strike_app/build

cp -R assets \
	layout \
	user_data \
	LICENSE \
	Autona.exe \
	Autona/

cp -R stage_strike_app/build \
	Autona/stage_strike_app/

rm -rf \
	Autona/assets/versions.json \
	Autona/assets/contributors.txt \
	Autona/layout/game_images \
	Autona/layout/game_screenshots

zip -rv release-windows.zip Autona

rm -rf Autona

popd > /dev/null