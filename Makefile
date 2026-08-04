.PHONY: up down build migrate createsuperuser create-starter-path test collectstatic logs

up:
	docker compose up

down:
	docker compose down

build:
	docker compose build

migrate:
	docker compose run --rm web python manage.py migrate

createsuperuser:
	docker compose run --rm web python manage.py createsuperuser

create-starter-path:
	docker compose run --rm web python manage.py create_starter_path

test:
	docker compose run --rm web python manage.py test

collectstatic:
	docker compose run --rm web python manage.py collectstatic --noinput

logs:
	docker compose logs -f web
