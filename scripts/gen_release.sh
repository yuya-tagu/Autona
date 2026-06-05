#!/bin/bash

pushd .. > /dev/null

# Create Autona dir and its web folders
mkdir -p Autona/web/build

cp -R assets \
	layout \
	user_data \
	LICENSE \
	Autona.exe \
	Autona/

cp -R web/build \
	Autona/web/

rm -rf \
	Autona/assets/versions.json \
	Autona/layout/game_images \
	Autona/layout/game_screenshots

zip -rv release-windows.zip Autona

rm -rf Autona

popd > /dev/null