"""Boshlang'ich baza tuzilmasi — barcha 12 jadval

Revision ID: 0001_initial
Revises:
Create Date: 2026-08-08

"""
from alembic import op
import sqlalchemy as sa

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── enum turlari ──
    op.execute("CREATE TYPE role AS ENUM (\'owner\', \'partner\', \'worker\')")
    op.execute("CREATE TYPE orderstatus AS ENUM (\'running\', \'finished\', \'cancelled\')")
    op.execute("""CREATE TYPE lanestatus AS ENUM (
        \'queued\', \'getting_number\', \'logging_in\', \'login_failed\',
        \'waiting_stuck\', \'got_code\', \'logging_full\', \'premium_pending\',
        \'otp_waiting\', \'checking\', \'confirmed\', \'failed\'
    )""")

    op.execute("""
    CREATE TABLE users (
    	id SERIAL NOT NULL, 
    	tg_id BIGINT NOT NULL, 
    	name VARCHAR(128) NOT NULL, 
    	username VARCHAR(64), 
    	role role NOT NULL, 
    	parent_id INTEGER, 
    	price_per_premium INTEGER NOT NULL, 
    	share_percent INTEGER NOT NULL, 
    	pin_hash VARCHAR(128), 
    	joined_channel BOOLEAN NOT NULL, 
    	created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL, 
    	PRIMARY KEY (id), 
    	FOREIGN KEY(parent_id) REFERENCES users (id)
    )
    """)

    op.execute("""
    CREATE TABLE daily_stats (
    	id SERIAL NOT NULL, 
    	owner_id INTEGER NOT NULL, 
    	date TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
    	hour INTEGER NOT NULL, 
    	count INTEGER NOT NULL, 
    	PRIMARY KEY (id), 
    	FOREIGN KEY(owner_id) REFERENCES users (id)
    )
    """)

    op.execute("""
    CREATE TABLE invite_codes (
    	id SERIAL NOT NULL, 
    	code VARCHAR(20) NOT NULL, 
    	kind VARCHAR(10) NOT NULL, 
    	created_by INTEGER NOT NULL, 
    	used BOOLEAN NOT NULL, 
    	used_by_id INTEGER, 
    	created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL, 
    	PRIMARY KEY (id), 
    	FOREIGN KEY(created_by) REFERENCES users (id), 
    	FOREIGN KEY(used_by_id) REFERENCES users (id)
    )
    """)

    op.execute("""
    CREATE TABLE number_bots (
    	id SERIAL NOT NULL, 
    	owner_id INTEGER NOT NULL, 
    	username VARCHAR(64) NOT NULL, 
    	slot INTEGER NOT NULL, 
    	max_logins INTEGER NOT NULL, 
    	active_logins INTEGER NOT NULL, 
    	connected BOOLEAN NOT NULL, 
    	PRIMARY KEY (id), 
    	FOREIGN KEY(owner_id) REFERENCES users (id)
    )
    """)

    op.execute("""
    CREATE TABLE people (
    	id SERIAL NOT NULL, 
    	owner_id INTEGER NOT NULL, 
    	name VARCHAR(128) NOT NULL, 
    	created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL, 
    	PRIMARY KEY (id), 
    	FOREIGN KEY(owner_id) REFERENCES users (id)
    )
    """)

    op.execute("""
    CREATE TABLE proxies (
    	id SERIAL NOT NULL, 
    	owner_id INTEGER NOT NULL, 
    	kind VARCHAR(10) NOT NULL, 
    	host VARCHAR(128) NOT NULL, 
    	port INTEGER NOT NULL, 
    	username VARCHAR(64), 
    	password_enc TEXT, 
    	in_use BOOLEAN NOT NULL, 
    	created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL, 
    	PRIMARY KEY (id), 
    	FOREIGN KEY(owner_id) REFERENCES users (id)
    )
    """)

    op.execute("""
    CREATE TABLE relay_devices (
    	id SERIAL NOT NULL, 
    	owner_id INTEGER NOT NULL, 
    	token VARCHAR(64) NOT NULL, 
    	device_model VARCHAR(128), 
    	last_seen_at TIMESTAMP WITHOUT TIME ZONE, 
    	created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL, 
    	PRIMARY KEY (id), 
    	FOREIGN KEY(owner_id) REFERENCES users (id)
    )
    """)

    op.execute("""
    CREATE TABLE access_accounts (
    	id SERIAL NOT NULL, 
    	owner_id INTEGER NOT NULL, 
    	phone VARCHAR(20) NOT NULL, 
    	session_file VARCHAR(256) NOT NULL, 
    	proxy_id INTEGER, 
    	connected BOOLEAN NOT NULL, 
    	created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL, 
    	PRIMARY KEY (id), 
    	FOREIGN KEY(owner_id) REFERENCES users (id), 
    	FOREIGN KEY(proxy_id) REFERENCES proxies (id)
    )
    """)

    op.execute("""
    CREATE TABLE cards (
    	id SERIAL NOT NULL, 
    	person_id INTEGER NOT NULL, 
    	bank_id VARCHAR(32) NOT NULL, 
    	number_enc TEXT NOT NULL, 
    	exp VARCHAR(7) NOT NULL, 
    	cvv_enc TEXT NOT NULL, 
    	holder_name VARCHAR(128) NOT NULL, 
    	"limit" INTEGER NOT NULL, 
    	used INTEGER NOT NULL, 
    	created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL, 
    	PRIMARY KEY (id), 
    	FOREIGN KEY(person_id) REFERENCES people (id)
    )
    """)

    op.execute("""
    CREATE TABLE orders (
    	id SERIAL NOT NULL, 
    	owner_id INTEGER NOT NULL, 
    	person_id INTEGER NOT NULL, 
    	status orderstatus NOT NULL, 
    	created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL, 
    	finished_at TIMESTAMP WITHOUT TIME ZONE, 
    	PRIMARY KEY (id), 
    	FOREIGN KEY(owner_id) REFERENCES users (id), 
    	FOREIGN KEY(person_id) REFERENCES people (id)
    )
    """)

    op.execute("""
    CREATE TABLE lanes (
    	id SERIAL NOT NULL, 
    	order_id INTEGER NOT NULL, 
    	card_id INTEGER NOT NULL, 
    	bot_id INTEGER NOT NULL, 
    	proxy_id INTEGER, 
    	status lanestatus NOT NULL, 
    	phone_number VARCHAR(32), 
    	login_code VARCHAR(16), 
    	login_pass VARCHAR(64), 
    	session_file VARCHAR(256), 
    	otp_code VARCHAR(16), 
    	error_reason TEXT, 
    	started_at TIMESTAMP WITHOUT TIME ZONE, 
    	finished_at TIMESTAMP WITHOUT TIME ZONE, 
    	PRIMARY KEY (id), 
    	FOREIGN KEY(order_id) REFERENCES orders (id), 
    	FOREIGN KEY(card_id) REFERENCES cards (id), 
    	FOREIGN KEY(bot_id) REFERENCES number_bots (id), 
    	FOREIGN KEY(proxy_id) REFERENCES proxies (id)
    )
    """)

    op.execute("""
    CREATE TABLE relay_sms (
    	id SERIAL NOT NULL, 
    	device_id INTEGER NOT NULL, 
    	lane_id INTEGER, 
    	sender VARCHAR(64) NOT NULL, 
    	body TEXT NOT NULL, 
    	extracted_code VARCHAR(16), 
    	matched BOOLEAN NOT NULL, 
    	received_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL, 
    	PRIMARY KEY (id), 
    	FOREIGN KEY(device_id) REFERENCES relay_devices (id), 
    	FOREIGN KEY(lane_id) REFERENCES lanes (id)
    )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS relay_sms, lanes, orders, cards, access_accounts, relay_devices, proxies, people, number_bots, invite_codes, daily_stats, users CASCADE")
    op.execute("DROP TYPE IF EXISTS lanestatus")
    op.execute("DROP TYPE IF EXISTS orderstatus")
    op.execute("DROP TYPE IF EXISTS role")
