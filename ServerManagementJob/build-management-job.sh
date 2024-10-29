poetry export -f requirements.txt > requirements.txt --without-hashes
pip install -t ./package -r requirements.txt
cd package
zip -r ../ServerManagementJob.zip .
cd ../src
zip -r ../ServerManagementJob.zip .
