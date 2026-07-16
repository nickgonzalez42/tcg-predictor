using System;
using Microsoft.EntityFrameworkCore.Migrations;

#nullable disable

namespace API.Data.Migrations
{
    /// <inheritdoc />
    public partial class AddCardAlerts : Migration
    {
        /// <inheritdoc />
        protected override void Up(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.CreateTable(
                name: "CardAlerts",
                columns: table => new
                {
                    Id = table.Column<int>(type: "INTEGER", nullable: false)
                        .Annotation("Sqlite:Autoincrement", true),
                    UserName = table.Column<string>(type: "TEXT", nullable: false),
                    Game = table.Column<string>(type: "TEXT", nullable: false),
                    ProductId = table.Column<int>(type: "INTEGER", nullable: false),
                    Grade = table.Column<string>(type: "TEXT", nullable: true),
                    Kind = table.Column<string>(type: "TEXT", nullable: false),
                    Horizon = table.Column<string>(type: "TEXT", nullable: true),
                    Direction = table.Column<string>(type: "TEXT", nullable: false),
                    Target = table.Column<double>(type: "REAL", nullable: false),
                    CreatedAt = table.Column<DateTime>(type: "TEXT", nullable: false)
                },
                constraints: table =>
                {
                    table.PrimaryKey("PK_CardAlerts", x => x.Id);
                });

            migrationBuilder.CreateIndex(
                name: "IX_CardAlerts_UserName_Game_ProductId",
                table: "CardAlerts",
                columns: new[] { "UserName", "Game", "ProductId" });

            // Carry over the legacy single-alert-per-wishlist-row targets
            // (TrackedCards.AlertTargetPrice, "notify at-or-below price") so
            // nobody's existing alert disappears with the new multi-alert UI.
            migrationBuilder.Sql(
                "INSERT INTO CardAlerts (UserName, Game, ProductId, Grade, Kind, Horizon, Direction, Target, CreatedAt) " +
                "SELECT UserName, Game, ProductId, NULL, 'price', NULL, 'below', AlertTargetPrice, AddedAt " +
                "FROM TrackedCards WHERE Kind = 'wishlist' AND AlertTargetPrice IS NOT NULL");
        }

        /// <inheritdoc />
        protected override void Down(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.DropTable(
                name: "CardAlerts");
        }
    }
}
