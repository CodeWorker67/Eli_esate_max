from sqlalchemy import BigInteger, String, Boolean, ForeignKey, Integer, Text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, mapped_column, Mapped, relationship
import atexit
import datetime


con_string = 'sqlite+aiosqlite:///db/database.db'

engine = create_async_engine(con_string)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)

atexit.register(engine.dispose)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = 'user'
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[str] = mapped_column(nullable=True)
    first_name: Mapped[str] = mapped_column(nullable=True)
    last_name: Mapped[str] = mapped_column(nullable=True)
    time_start: Mapped[datetime.datetime] = mapped_column(nullable=True)
    is_active: Mapped[bool] = mapped_column(default=True)


class Manager(Base):
    __tablename__ = 'manager'
    id: Mapped[int] = mapped_column(primary_key=True)
    start_key: Mapped[str] = mapped_column(String(20), nullable=True)
    phone: Mapped[str] = mapped_column(String(20), nullable=True)
    fio: Mapped[str] = mapped_column(nullable=True)
    tg_id: Mapped[int] = mapped_column(BigInteger, nullable=True)
    username: Mapped[str] = mapped_column(nullable=True)
    first_name: Mapped[str] = mapped_column(nullable=True)
    last_name: Mapped[str] = mapped_column(nullable=True)
    time_add_to_db: Mapped[datetime.datetime] = mapped_column(default=datetime.datetime.now)
    time_registration: Mapped[datetime.datetime] = mapped_column(nullable=True)
    status: Mapped[bool] = mapped_column(Boolean, default=False)


class Security(Base):
    __tablename__ = 'security'
    id: Mapped[int] = mapped_column(primary_key=True)
    start_key: Mapped[str] = mapped_column(String(20), nullable=True)
    phone: Mapped[str] = mapped_column(String(20), nullable=True)
    fio: Mapped[str] = mapped_column(nullable=True)
    tg_id: Mapped[int] = mapped_column(BigInteger, nullable=True)
    username: Mapped[str] = mapped_column(nullable=True)
    first_name: Mapped[str] = mapped_column(nullable=True)
    last_name: Mapped[str] = mapped_column(nullable=True)
    time_add_to_db: Mapped[datetime.datetime] = mapped_column(default=datetime.datetime.now)
    time_registration: Mapped[datetime.datetime] = mapped_column(nullable=True)
    status: Mapped[bool] = mapped_column(Boolean, default=False)


class Resident(Base):
    __tablename__ = 'resident'
    id: Mapped[int] = mapped_column(primary_key=True)
    start_key: Mapped[str] = mapped_column(String(20), nullable=True)
    phone: Mapped[str] = mapped_column(String(20), nullable=True)
    fio: Mapped[str] = mapped_column(nullable=True)
    plot_number: Mapped[str] = mapped_column(nullable=True)
    tg_id: Mapped[int] = mapped_column(BigInteger, nullable=True)
    username: Mapped[str] = mapped_column(nullable=True)
    first_name: Mapped[str] = mapped_column(nullable=True)
    last_name: Mapped[str] = mapped_column(nullable=True)
    time_add_to_db: Mapped[datetime.datetime] = mapped_column(default=datetime.datetime.now)
    time_registration: Mapped[datetime.datetime] = mapped_column(nullable=True)
    status: Mapped[bool] = mapped_column(Boolean, default=False)
    requests = relationship("RegistrationRequest", back_populates="resident")


class Contractor(Base):
    __tablename__ = 'contractor'
    id: Mapped[int] = mapped_column(primary_key=True)
    start_key: Mapped[str] = mapped_column(String(20), nullable=True)
    phone: Mapped[str] = mapped_column(String(20), nullable=True)
    work_types: Mapped[str] = mapped_column(nullable=True)  # Добавлено
    company: Mapped[str] = mapped_column(nullable=True)  # Добавлено
    position: Mapped[str] = mapped_column(nullable=True)  # Добавлено
    fio: Mapped[str] = mapped_column(nullable=True)
    affiliation: Mapped[str] = mapped_column(nullable=True)
    tg_id: Mapped[int] = mapped_column(BigInteger, nullable=True)
    username: Mapped[str] = mapped_column(nullable=True)
    first_name: Mapped[str] = mapped_column(nullable=True)
    last_name: Mapped[str] = mapped_column(nullable=True)
    time_add_to_db: Mapped[datetime.datetime] = mapped_column(default=datetime.datetime.now)
    time_registration: Mapped[datetime.datetime] = mapped_column(nullable=True)
    status: Mapped[bool] = mapped_column(Boolean, default=False)
    can_add_contractor: Mapped[bool] = mapped_column(Boolean, default=False)
    requests = relationship("ContractorRegistrationRequest", back_populates="contractor")


class RegistrationRequest(Base):
    __tablename__ = 'registration_request'
    id: Mapped[int] = mapped_column(primary_key=True)
    resident_id: Mapped[int] = mapped_column(ForeignKey('resident.id'))
    fio: Mapped[str] = mapped_column(nullable=True)
    plot_number: Mapped[str] = mapped_column(nullable=True)
    photo_id: Mapped[str] = mapped_column(nullable=True)
    tg_id: Mapped[int] = mapped_column(BigInteger, nullable=True)
    username: Mapped[str] = mapped_column(nullable=True)
    first_name: Mapped[str] = mapped_column(nullable=True)
    last_name: Mapped[str] = mapped_column(nullable=True)
    status: Mapped[str] = mapped_column(default='pending') # pending/approved/rejected
    admin_comment: Mapped[str] = mapped_column(nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(default=datetime.datetime.now)
    resident = relationship("Resident", back_populates="requests")


Resident.requests = relationship("RegistrationRequest", back_populates="resident", order_by=RegistrationRequest.id)


class ContractorRegistrationRequest(Base):
    __tablename__ = 'contractor_registration_request'
    id: Mapped[int] = mapped_column(primary_key=True)
    company: Mapped[str] = mapped_column(nullable=True)  # Добавлено
    position: Mapped[str] = mapped_column(nullable=True)  # Добавлено
    contractor_id: Mapped[int] = mapped_column(ForeignKey('contractor.id'), nullable=True)
    fio: Mapped[str] = mapped_column(nullable=True)
    affiliation: Mapped[str] = mapped_column(default='УК')
    tg_id: Mapped[int] = mapped_column(BigInteger, nullable=True)
    username: Mapped[str] = mapped_column(nullable=True)
    first_name: Mapped[str] = mapped_column(nullable=True)
    last_name: Mapped[str] = mapped_column(nullable=True)
    status: Mapped[str] = mapped_column(default='pending')
    admin_comment: Mapped[str] = mapped_column(nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(default=datetime.datetime.now)
    contractor = relationship("Contractor", back_populates="requests")


Contractor.requests = relationship("ContractorRegistrationRequest", back_populates="contractor", order_by=ContractorRegistrationRequest.id)


class ResidentContractorRequest(Base):
    __tablename__ = 'resident_contractor_request'
    id: Mapped[int] = mapped_column(primary_key=True)
    resident_id: Mapped[int] = mapped_column(ForeignKey('resident.id'))
    phone: Mapped[str] = mapped_column(String(20))
    work_types: Mapped[str] = mapped_column(nullable=True)
    status: Mapped[str] = mapped_column(default='pending')
    admin_comment: Mapped[str] = mapped_column(nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(default=datetime.datetime.now)
    resident = relationship("Resident")


class ContractorContractorRequest(Base):
    __tablename__ = 'contractor_contractor_request'
    id: Mapped[int] = mapped_column(primary_key=True)
    contractor_id: Mapped[int] = mapped_column(ForeignKey('contractor.id'))
    phone: Mapped[str] = mapped_column(String(20))
    work_types: Mapped[str] = mapped_column(nullable=True)
    status: Mapped[str] = mapped_column(default='pending')
    admin_comment: Mapped[str] = mapped_column(nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(default=datetime.datetime.now)
    contractor = relationship("Contractor")


class PermanentPass(Base):
    __tablename__ = 'permanent_pass'
    id: Mapped[int] = mapped_column(primary_key=True)
    resident_id: Mapped[int] = mapped_column(ForeignKey('resident.id'), nullable=True)
    car_brand: Mapped[str] = mapped_column(nullable=True)      # Марка машины
    car_model: Mapped[str] = mapped_column(nullable=True)     # Модель машины
    car_number: Mapped[str] = mapped_column(nullable=True)    # Номер машины
    car_owner: Mapped[str] = mapped_column(nullable=True)     # Кому принадлежит машина?
    status: Mapped[str] = mapped_column(default='pending')    # pending/approved/rejected
    resident_comment: Mapped[str] = mapped_column(nullable=True)   # Комментарий резиденту
    security_comment: Mapped[str] = mapped_column(nullable=True)   # Комментарий для СБ
    destination: Mapped[str] = mapped_column(nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(default=datetime.datetime.now)
    time_registration: Mapped[datetime.datetime] = mapped_column(nullable=True)
    resident = relationship("Resident")


class TemporaryPass(Base):
    __tablename__ = 'temporary_pass'

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_type: Mapped[str] = mapped_column(String(20))  # resident/contractor
    resident_id: Mapped[int] = mapped_column(ForeignKey('resident.id'), nullable=True)
    contractor_id: Mapped[int] = mapped_column(ForeignKey('contractor.id'), nullable=True)
    vehicle_type: Mapped[str] = mapped_column(String(20))  # car/truck
    weight_category: Mapped[str] = mapped_column(String(20), nullable=True)  # light/heavy
    length_category: Mapped[str] = mapped_column(String(20), nullable=True)  # short/long
    car_number: Mapped[str] = mapped_column(String(20))
    car_brand: Mapped[str] = mapped_column(String(50))
    cargo_type: Mapped[str] = mapped_column(String(100), nullable=True)
    purpose: Mapped[str] = mapped_column(String(100))
    visit_date: Mapped[datetime.date] = mapped_column()
    owner_comment: Mapped[str] = mapped_column(nullable=True)
    resident_comment: Mapped[str] = mapped_column(nullable=True)
    security_comment: Mapped[str] = mapped_column(nullable=True)
    status: Mapped[str] = mapped_column(default='pending')  # pending/approved/rejected
    destination: Mapped[str] = mapped_column(nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(default=datetime.datetime.now)
    time_registration: Mapped[datetime.datetime] = mapped_column(nullable=True)

    resident = relationship("Resident")
    contractor = relationship("Contractor")


class TempPassYooKassaPayment(Base):
    """Платежи ЮKassa за временный грузовой пропуск (новая таблица, существующие не трогаем)."""
    __tablename__ = 'temp_pass_yookassa_payment'

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    temporary_pass_id: Mapped[int] = mapped_column(ForeignKey('temporary_pass.id'), nullable=False)
    yookassa_payment_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    amount_kopeks: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default='pending')
    confirmation_url: Mapped[str] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(default=datetime.datetime.now)
    paid_at: Mapped[datetime.datetime] = mapped_column(nullable=True)


class Appeal(Base):
    __tablename__ = 'appeal'
    id: Mapped[int] = mapped_column(primary_key=True)
    request_text: Mapped[str] = mapped_column(nullable=False)
    response_text: Mapped[str] = mapped_column(nullable=True)
    resident_id: Mapped[int] = mapped_column(ForeignKey('resident.id'))
    responser_id: Mapped[int] = mapped_column(ForeignKey('user.id'), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(default=datetime.datetime.now)
    responsed_at: Mapped[datetime.datetime] = mapped_column(nullable=True)
    status: Mapped[bool] = mapped_column(default=False)  # False - ожидание, True - закрыто

    resident = relationship("Resident")
    responser = relationship("User")


async def create_tables():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
