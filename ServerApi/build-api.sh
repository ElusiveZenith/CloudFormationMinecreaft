poetry export -f requirements.txt > requirements.txt --without-hashes
pip install -t ./package -r requirements.txt
cd package
zip -r ../ServerApi.zip .
cd ../src
zip -r ../ServerApi.zip .
